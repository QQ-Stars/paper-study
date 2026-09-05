import type { Paper, ReproductionProject } from '../api/types';

export const PROJECT_PAPER_RESULT_LIMIT = 60;

export function paperDisplayTitle(paper: Paper): string {
  return paper.title_zh?.trim() || paper.title?.trim() || paper.id;
}

function normalized(value: unknown): string {
  return String(value ?? '').normalize('NFKC').trim().toLocaleLowerCase('zh-CN');
}

function searchText(paper: Paper): string {
  return normalized([
    paper.id,
    paper.title,
    paper.title_zh,
    paper.venue,
    paper.year,
    paper.topic,
    paper.type,
    paper.source,
  ].filter(Boolean).join(' '));
}

function recencyScore(paper: Paper): number {
  const created = Date.parse(paper.created_at || '');
  const createdScore = Number.isFinite(created) ? created / 100_000_000_000 : 0;
  return (paper.favorite === 1 ? 40 : 0)
    + (paper.status === '已理解' ? 18 : paper.status === '学习中' ? 10 : 0)
    + Math.max(0, Math.min(10, (paper.relevance ?? 0) * 10))
    + createdScore;
}

function matchScore(paper: Paper, query: string): number {
  const title = normalized(paperDisplayTitle(paper));
  const originalTitle = normalized(paper.title);
  const id = normalized(paper.id);
  const tokens = normalized(query).split(/\s+/).filter(Boolean);
  const haystack = searchText(paper);
  if (!tokens.every((token) => haystack.includes(token))) return Number.NEGATIVE_INFINITY;

  const compactQuery = tokens.join(' ');
  let score = recencyScore(paper);
  if (id === compactQuery) score += 900;
  if (title === compactQuery || originalTitle === compactQuery) score += 700;
  if (title.startsWith(compactQuery) || originalTitle.startsWith(compactQuery)) score += 420;
  if (title.includes(compactQuery) || originalTitle.includes(compactQuery)) score += 240;
  score += tokens.reduce((total, token) => total + (title.includes(token) ? 35 : 0), 0);
  return score;
}

export function filterProjectPapers(
  papers: Paper[],
  query: string,
  limit = PROJECT_PAPER_RESULT_LIMIT,
): { items: Paper[]; total: number } {
  const normalizedQuery = normalized(query);
  const ranked = papers
    .map((paper) => ({ paper, score: normalizedQuery ? matchScore(paper, normalizedQuery) : recencyScore(paper) }))
    .filter((entry) => Number.isFinite(entry.score))
    .sort((left, right) => right.score - left.score
      || paperDisplayTitle(left.paper).localeCompare(paperDisplayTitle(right.paper), 'zh-CN'));
  return {
    items: ranked.slice(0, Math.max(1, limit)).map((entry) => entry.paper),
    total: ranked.length,
  };
}

export function findReproductionProjectForPaper(
  projects: ReproductionProject[],
  paperId: string,
): ReproductionProject | undefined {
  const target = paperId.trim();
  if (!target) return undefined;
  return projects.find(
    (project) => project.projectKind === 'reproduction' && project.paperId === target,
  );
}
