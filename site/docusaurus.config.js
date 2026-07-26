// @ts-check
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

/**
 * Local validation artifact only. The source book is copyright Cambridge
 * University Press; see the repository README under "Licensing". Do not
 * deploy this site publicly.
 *
 * @type {import('@docusaurus/types').Config}
 */
const config = {
  title: 'Networks, Crowds, and Markets',
  tagline: 'Local fidelity-validation build - not for publication',
  url: 'http://localhost',
  baseUrl: '/',

  // A broken link or anchor means a cross-reference lost its target, which is
  // a fidelity failure. The build must refuse to emit rather than warn.
  onBrokenLinks: 'throw',
  onBrokenAnchors: 'throw',

  markdown: {
    // Chapter bodies are emitted as .md and must parse as CommonMark. This
    // book's prose is full of bare <, { and }, each of which is a build error
    // under MDX. Remark plugins still run, so math continues to work.
    format: 'detect',
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          path: 'docs',
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          // Every equation this pipeline emits is `$$...$$` display math (see
          // pipeline/stage4_emit.py); it never writes single-dollar inline
          // math. Left at the default, remark-math still treats a single "$"
          // as an inline-math delimiter, and the book's prose is full of
          // plain currency figures -- two or more in the same paragraph
          // ("...$80,000 of revenue... $40,000 of revenue...", Chapter 22)
          // then read as one opening and one closing delimiter, with
          // everything between misparsed as a formula. `singleDollarTextMath:
          // false` restricts inline math to `$$...$$`, which this pipeline
          // never emits inline anyway, so plain currency text is never
          // captured as math again.
          remarkPlugins: [[remarkMath, {singleDollarTextMath: false}]],
          rehypePlugins: [rehypeKatex],
        },
        blog: false,
        theme: {
          // KaTeX stylesheet is imported from node_modules in custom.css so
          // the build stays deterministic and works offline.
          customCss: './src/css/custom.css',
        },
      }),
    ],
  ],

  themeConfig: {
    navbar: {
      title: 'Networks, Crowds, and Markets',
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'book',
          position: 'left',
          label: 'Contents',
        },
      ],
    },
    footer: {
      style: 'dark',
      copyright:
        'D. Easley and J. Kleinberg. Networks, Crowds, and Markets: Reasoning about a Highly Connected World. Cambridge University Press, 2010. Draft version: June 10, 2010.',
    },
  },
};

export default config;
