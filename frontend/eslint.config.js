import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const legacyDynamicImportSelector = String.raw`ImportExpression[source.value=/((^|\/)(public|legacy)(\/|$))/]`;

export default tseslint.config(
  {
    ignores: ['dist/**', 'coverage/**', 'playwright-report/**', 'test-results/**'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [reactHooks.configs.flat.recommended, reactRefresh.configs.vite],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                'public',
                'public/**',
                '/public/**',
                '**/public/**',
                'legacy',
                'legacy/**',
                '/legacy/**',
                '**/legacy/**',
              ],
              message: 'The React workspace cannot import legacy application assets.',
            },
          ],
        },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector: legacyDynamicImportSelector,
          message: 'The React workspace cannot dynamically import legacy application assets.',
        },
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'Raw HTML is restricted to the TrustedMathHtml adapter.',
        },
      ],
    },
  },
  {
    files: ['src/**/*.{test,spec}.{ts,tsx}', 'src/test/**/*.{ts,tsx}'],
    languageOptions: {
      globals: globals.vitest,
    },
  },
  {
    files: ['src/lib/markdown/TrustedMathHtml.tsx'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: legacyDynamicImportSelector,
          message: 'The React workspace cannot dynamically import legacy application assets.',
        },
      ],
    },
  },
  {
    files: ['vite.config.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },
);
