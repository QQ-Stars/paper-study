import { access } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const showcaseRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const requiredRoutes = [
  ['论文复现', 'public/categories/论文复现/index.html'],
  ['文章 / 博客', 'public/categories/文章/index.html'],
  ['分类', 'public/categories/index.html'],
  ['标签', 'public/tags/index.html'],
];

const missing = [];
for (const [label, relativePath] of requiredRoutes) {
  try {
    await access(path.join(showcaseRoot, relativePath));
  } catch {
    missing.push(`${label}: ${relativePath}`);
  }
}

if (missing.length) {
  throw new Error(`公开导航存在未生成的页面：\n${missing.join('\n')}`);
}

console.log(`公开导航检查通过（${requiredRoutes.length} 个页面）。`);
