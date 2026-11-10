import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { Plugin } from 'vite';

const publicShell = [
  'index.html',
  'manifest.webmanifest',
  'icons/favicon.svg',
  'icons/apple-touch-icon.png',
  'icons/marketpulse-192.png',
  'icons/marketpulse-512.png',
  'icons/marketpulse-maskable.png',
];

export function marketPulsePwa(): Plugin {
  return {
    name: 'marketpulse-pwa',
    apply: 'build',
    async writeBundle({ dir }, bundle) {
      if (!dir) throw new Error('The PWA build requires a static output directory.');

      const assets = Object.keys(bundle).filter((name) => /^assets\/[\w-]+\.(?:js|css)$/.test(name));
      const files = await Promise.all([...publicShell, ...assets.sort()].map(async (name) => {
        const content = await readFile(join(dir, name));
        const hash = createHash('sha256').update(content).digest('base64');
        return [name, `sha256-${hash}`];
      }));
      const worker = await readFile(new URL('./service-worker.js', import.meta.url), 'utf8');
      const manifest = JSON.stringify(files);
      const version = createHash('sha256').update(manifest).update(worker).digest('hex');
      await writeFile(join(dir, 'sw.js'), `const version = '${version}';\nconst shell = ${manifest};\n${worker}`);
    },
  };
}
