/**
 * IDF-weighted name scoring.
 *
 * The problem this fixes: "Maria Gonzalez" and "John Smith" were matching
 * because token overlap was counted without regard to how much information
 * each token carries. "Gonzalez" appears on hundreds of designated parties.
 * "Pridmore" appears on one. They should not count the same.
 *
 * Two changes:
 *   1. Every token is weighted by inverse document frequency, computed from
 *      your own corpus during the nightly build.
 *   2. Unmatched tokens on the CANDIDATE side are penalised. Matching two of
 *      four names is not a match, even when both matched tokens are exact.
 *
 * Drop this in as functions/api/v1/_score.js and import scoreName from screen.js
 * and search.js so both endpoints score identically.
 */

// How hard to penalise leftover tokens on the candidate side.
// 0 = ignore them entirely (old behaviour), 1 = require full coverage.
const COVERAGE_WEIGHT = 0.35;

// Two tokens count as the same token above this similarity.
// Catches Putin/Poutine, Vladimirovich/Vladimirovic.
const TOKEN_SIM_FLOOR = 0.86;

const CORPORATE_SUFFIXES = new Set([
  "llc","inc","ltd","limited","corp","corporation","co","company","plc","gmbh",
  "ag","sa","srl","spa","bv","nv","ab","as","oy","pte","pty","sarl","sas",
  "oao","ooo","pao","zao","jsc","ojsc","cjsc","pjsc","llp","lp",
]);

const HONORIFICS = new Set([
  "mr","mrs","ms","dr","prof","sir","gen","general","col","colonel","maj",
  "major","capt","captain","lt","sgt","brig","adm","admiral","sheikh","shaykh",
  "haji","hajji","sayyid","mullah",
]);

export function normalize(raw) {
  return (raw || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")   // strip accents
    .toLowerCase()
    .replace(/['`’]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")      // hyphens become spaces
    .replace(/\s+/g, " ")
    .trim();
}

export function tokenize(raw) {
  const tokens = normalize(raw)
    .split(" ")
    .filter(t => t.length > 0 && !HONORIFICS.has(t));
  const stripped = tokens.filter(t => !CORPORATE_SUFFIXES.has(t));
  return stripped.length ? stripped : tokens;
}

// Jaro-Winkler. Small, no dependency, good on short strings like names.
function jaroWinkler(a, b) {
  if (a === b) return 1;
  if (!a.length || !b.length) return 0;

  const window = Math.max(0, Math.floor(Math.max(a.length, b.length) / 2) - 1);
  const aFlags = new Array(a.length).fill(false);
  const bFlags = new Array(b.length).fill(false);
  let matches = 0;

  for (let i = 0; i < a.length; i++) {
    const start = Math.max(0, i - window);
    const end = Math.min(i + window + 1, b.length);
    for (let j = start; j < end; j++) {
      if (bFlags[j] || a[i] !== b[j]) continue;
      aFlags[i] = bFlags[j] = true;
      matches++;
      break;
    }
  }
  if (!matches) return 0;

  let transpositions = 0;
  let k = 0;
  for (let i = 0; i < a.length; i++) {
    if (!aFlags[i]) continue;
    while (!bFlags[k]) k++;
    if (a[i] !== b[k]) transpositions++;
    k++;
  }
  transpositions /= 2;

  const jaro =
    (matches / a.length + matches / b.length +
     (matches - transpositions) / matches) / 3;

  let prefix = 0;
  for (let i = 0; i < Math.min(4, a.length, b.length); i++) {
    if (a[i] !== b[i]) break;
    prefix++;
  }
  return jaro + prefix * 0.1 * (1 - jaro);
}

/**
 * idfMap: { token: weight }, produced by the nightly build.
 * Unknown tokens get the default, which should be high — a token absent from
 * the corpus is by definition rare and therefore informative.
 */
export function weightOf(token, idfMap, fallback = 6.0) {
  const w = idfMap[token];
  return w === undefined ? fallback : w;
}

/**
 * Score one subject name against one candidate name.
 * Returns { score, matchedTokens, subjectCoverage, candidateCoverage }.
 */
export function scoreName(subjectTokens, candidateTokens, idfMap) {
  if (!subjectTokens.length || !candidateTokens.length) {
    return { score: 0, matchedTokens: [], subjectCoverage: 0, candidateCoverage: 0 };
  }

  const subjectWeights = subjectTokens.map(t => weightOf(t, idfMap));
  const candidateWeights = candidateTokens.map(t => weightOf(t, idfMap));
  const subjectTotal = subjectWeights.reduce((a, b) => a + b, 0);
  const candidateTotal = candidateWeights.reduce((a, b) => a + b, 0);

  // Greedy best-pair matching. Names are short, so O(n*m) is fine.
  const usedCandidate = new Set();
  const matched = [];
  let matchedSubjectWeight = 0;
  let matchedCandidateWeight = 0;

  for (let i = 0; i < subjectTokens.length; i++) {
    let bestJ = -1;
    let bestSim = 0;
    for (let j = 0; j < candidateTokens.length; j++) {
      if (usedCandidate.has(j)) continue;
      const sim = jaroWinkler(subjectTokens[i], candidateTokens[j]);
      if (sim > bestSim) { bestSim = sim; bestJ = j; }
    }
    // An initial matches any token starting with that letter.
    if (bestJ >= 0 && subjectTokens[i].length === 1) {
      const cand = candidateTokens[bestJ];
      if (cand.startsWith(subjectTokens[i])) bestSim = Math.max(bestSim, 0.9);
    }
    if (bestJ >= 0 && bestSim >= TOKEN_SIM_FLOOR) {
      usedCandidate.add(bestJ);
      matched.push({
        subject: subjectTokens[i],
        candidate: candidateTokens[bestJ],
        sim: Number(bestSim.toFixed(3)),
        weight: Number(subjectWeights[i].toFixed(2)),
      });
      matchedSubjectWeight += subjectWeights[i] * bestSim;
      matchedCandidateWeight += candidateWeights[bestJ] * bestSim;
    }
  }

  if (!matched.length) {
    return { score: 0, matchedTokens: [], subjectCoverage: 0, candidateCoverage: 0 };
  }

  const subjectCoverage = matchedSubjectWeight / subjectTotal;
  const candidateCoverage = matchedCandidateWeight / candidateTotal;

  // Weighted geometric mean. Subject coverage dominates, but leftover
  // high-information tokens on the candidate side drag the score down.
  const score =
    Math.pow(subjectCoverage, 1 - COVERAGE_WEIGHT) *
    Math.pow(candidateCoverage, COVERAGE_WEIGHT);

  return {
    score: Number(score.toFixed(3)),
    matchedTokens: matched,
    subjectCoverage: Number(subjectCoverage.toFixed(3)),
    candidateCoverage: Number(candidateCoverage.toFixed(3)),
  };
}

/**
 * Score a subject against every name on a candidate entity, including aliases.
 * Returns the best, and reports WHICH name matched — the thing the current
 * response is missing, and the reason some hits look inexplicable.
 *
 * entity.names is expected as [{ name, type }]. If your records only carry a
 * flat primary name plus an aliases array, adapt the loop below.
 */
export function scoreEntity(subjectRaw, entity, idfMap) {
  const subjectTokens = tokenize(subjectRaw);
  let best = null;

  const names = entity.names && entity.names.length
    ? entity.names
    : [{ name: entity.name, type: "primary" }];

  for (const n of names) {
    const result = scoreName(subjectTokens, tokenize(n.name), idfMap);
    if (!best || result.score > best.score) {
      best = { ...result, matchedName: n.name, matchedNameType: n.type || "primary" };
    }
  }
  return best;
}
