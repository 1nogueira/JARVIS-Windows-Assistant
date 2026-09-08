const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const root = __dirname;
const source = fs.readFileSync(path.join(root, 'i18n/messages.ts'), 'utf8');
const pairs = [...source.matchAll(/^  ("(?:[^"\\]|\\.)*"): ("(?:[^"\\]|\\.)*"),$/gm)].map(m => [JSON.parse(m[1]), JSON.parse(m[2])]);
const translations = new Map(pairs);
const english = new Map(pairs.map(([en, pt]) => [pt, en]));
const files = ['App.tsx','components/Shell.tsx','components/StatusOrb.tsx','components/SystemPulse.tsx','components/ChatComposer.tsx','components/CommandPalette.tsx','components/MessageBubble.tsx','pages/SettingsPage.tsx','pages/MemoryPage.tsx','pages/SystemPage.tsx','pages/ConversationPage.tsx'];
const missing = new Set();
for (const relative of files) {
  const filename = path.join(root, relative);
  const text = fs.readFileSync(filename, 'utf8');
  const file = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const changes = [];
  const functions = new Set();
  function translated(value) { const en = english.get(value) ?? value; if (!translations.has(en)) missing.add(en); return `t(${JSON.stringify(en)})`; }
  function owner(node) {
    for (let parent = node.parent; parent; parent = parent.parent) {
      if (ts.isFunctionDeclaration(parent) && /^[A-Z]/.test(parent.name?.text ?? '')) return parent;
    }
  }
  function replace(node, value) { const component = owner(node); if (!component) return; functions.add(component); changes.push([node.getStart(file), node.end, value]); }
  function visit(node) {
    if (ts.isJsxText(node)) {
      const raw = node.getText(file); const clean = raw.trim().replace(/\s+/g, ' ');
      if (/[A-Za-zÀ-ÿ]/.test(clean) && !['JARVIS','J','Ctrl K','Enter','Shift Enter','Esc','PID','CPU','GPU','MB','GB','ms','v'].includes(clean)) {
        const leading = /^\s/.test(raw) && !/^\s*\n/.test(raw) ? ' ' : '';
        const trailing = /\s$/.test(raw) && !/\n\s*$/.test(raw) ? ' ' : '';
        replace(node, `${leading}{${translated(clean)}}${trailing}`);
      }
    } else if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer) && ['title','aria-label','placeholder','label','description','eyebrow','detail'].includes(node.name.getText(file))) {
      replace(node.initializer, `{${translated(node.initializer.text)}}`);
    } else if (ts.isStringLiteral(node) && (translations.has(node.text) || english.has(node.text))) {
      const parent = node.parent;
      if ((ts.isConditionalExpression(parent) && parent.condition !== node) || (ts.isBinaryExpression(parent) && ['||','??'].includes(parent.operatorToken.getText(file)) && parent.right === node) || ts.isJsxExpression(parent)) replace(node, translated(node.text));
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  for (const fn of functions) changes.push([fn.body.getStart(file) + 1, fn.body.getStart(file) + 1, '\n  const { t } = useLanguage();']);
  let result = text;
  for (const [start,end,value] of changes.sort((a,b) => b[0]-a[0])) result = result.slice(0,start) + value + result.slice(end);
  if (functions.size) result = `import { useLanguage } from "${relative.includes('/') ? '../' : './'}i18n/LanguageContext";\n` + result;
  fs.writeFileSync(filename, result);
}
process.stdout.write(JSON.stringify([...missing].sort(), null, 2));
