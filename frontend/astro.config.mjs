import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  site: 'https://juulieen.github.io',
  base: '/RunEvent86/',
  build: {
    assets: '_assets',
  },
});
