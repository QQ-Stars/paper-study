'use strict';

const pagination = require('hexo-pagination');
const { slugize } = require('hexo-util');

// These are stable public navigation destinations produced by the backend
// exporter. Hexo's stock category generator skips categories with zero posts,
// so provide the same official Fluid category layout until the first post is
// published (and again if the last post is withdrawn).
const PUBLICATION_CATEGORIES = ['论文复现', '文章'];

function categoryPath(ctx, categoryName) {
  let categoryDir = ctx.config.category_dir || 'categories';
  if (categoryDir === '/') categoryDir = '';
  if (categoryDir && !categoryDir.endsWith('/')) categoryDir += '/';

  const categoryMap = ctx.config.category_map || {};
  const mappedName = categoryMap[categoryName] || categoryName;
  const slug = slugize(mappedName, { transform: ctx.config.filename_case });
  return `${categoryDir}${slug}/`;
}

hexo.extend.generator.register('publication-category-fallback', function (locals) {
  const emptyPosts = locals.posts.limit(0);

  return PUBLICATION_CATEGORIES.flatMap((categoryName) => {
    const category = locals.categories.findOne({ name: categoryName });
    if (category && category.length) return [];

    return pagination(categoryPath(this, categoryName), emptyPosts, {
      perPage: 0,
      layout: ['category', 'archive', 'index'],
      format: `${this.config.pagination_dir || 'page'}/%d/`,
      data: { category: categoryName },
    });
  });
});
