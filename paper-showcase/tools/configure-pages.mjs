import { writeFile } from 'node:fs/promises';

const origin = process.env.SHOWCASE_URL || 'https://qq-stars.github.io';
const root = process.env.SHOWCASE_ROOT || '/paper-study/';
const normalizedRoot = `/${root.replace(/^\/+|\/+$/g, '')}/`.replace(/^\/\/$/, '/');
const siteUrl = `${origin.replace(/\/$/, '')}${normalizedRoot === '/' ? '' : normalizedRoot.replace(/\/$/, '')}`;
const content = `url: ${JSON.stringify(siteUrl)}\nroot: ${JSON.stringify(normalizedRoot)}\n`;

await writeFile('.hexo-pages.yml', content, 'utf8');
