import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  site: 'https://juulieen.github.io',
  base: '/ou-court-le-club/',
  build: {
    assets: '_assets',
  },
});
