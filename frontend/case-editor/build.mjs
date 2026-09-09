import {build} from 'esbuild';
import {readFile, writeFile, readdir, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const out = path.join(root, 'static/vendor/tiptap/3.31.3');
await mkdir(out, {recursive:true});
const result = await build({
  absWorkingDir:here, entryPoints:[path.join(root,'static/js/departmental_exam_case_editor.js')],
  outfile:path.join(out,'tmp-case-editor.bundle.js'), bundle:true, format:'iife',
  target:['chrome110','firefox115','safari16'], minify:true, metafile:true, legalComments:'none',
  // Emit escaped strings rather than literal markdown hard-break spaces at EOL.
  // Preserve those significant spaces while keeping generated diffs checkable.
  supported:{'template-literal':false}
});
const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  if (!input.includes('node_modules/')) continue;
  let dir = path.dirname(path.resolve(here,input));
  while (dir.includes('node_modules')) {
    try {
      const manifest = JSON.parse(await readFile(path.join(dir,'package.json'),'utf8'));
      if (manifest.name && manifest.version) { packages.set(manifest.name, {dir,manifest}); break; }
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    dir = path.dirname(dir);
  }
}
let notices = 'TeacherMate+ Case editor: exact packages contributing to this bundle.\nNo Pro packages, cloud services, or license keys. Build tooling and DOM test dependencies are not shipped.\n';
for (const [name,{dir,manifest}] of [...packages].sort(([a],[b]) => a.localeCompare(b))) {
  if (manifest.license !== 'MIT' || name.startsWith('@tiptap-pro/')) throw new Error('Unapproved shipped license: '+name);
  const files = (await readdir(dir)).filter(file => /^licen[sc]e(?:\.|$)/i.test(file));
  if (!files.length) throw new Error('Missing license file: '+name);
  notices += `\n--- ${name}@${manifest.version} (${manifest.license}) ---\n`;
  for (const file of files) notices += await readFile(path.join(dir,file),'utf8') + '\n';
}
await writeFile(path.join(out,'THIRD_PARTY_NOTICES.txt'),notices.trimEnd() + '\n');
console.log(`Built self-hosted editor; ${packages.size} shipped packages, all MIT. License files retained.`);
