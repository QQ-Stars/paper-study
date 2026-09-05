'use strict';

const UNSAFE = /(\/api\/v2\/reproductions\/|file:\/\/|(?:[A-Za-z]:[\\/])|<\s*(?:script|iframe|object|embed|style|link)\b|\bon[a-z0-9_-]+\s*=|javascript\s*:|\{[%{])/is;
const SECRET = /(?:api[_ -]?key|access[_ -]?token|secret|password|authorization)\s*[:=]\s*[^\s,;]+/i;

hexo.extend.filter.register('before_generate', () => {
  const posts = hexo.locals.get('posts').toArray();
  const unsafe = posts.find((post) => {
    if (post.type !== 'reproduction' && post.type !== 'article') return false;
    const content = String(post.raw || post.content || '');
    return UNSAFE.test(content) || SECRET.test(content);
  });
  if (unsafe) {
    throw new Error(`SHOWCASE_PUBLIC_GUARD_FAILED: ${unsafe.source || unsafe.path}`);
  }
});
