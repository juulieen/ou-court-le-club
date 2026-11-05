// Rend une page avec un vrai navigateur (Chrome sans écran) et imprime son
// texte visible. Pour les sites d'organisateurs qui n'affichent rien sans
// JavaScript. Usage : node render_page.mjs <url>
// PLAYWRIGHT_DIR : dossier dont node_modules contient playwright.
import { createRequire } from "module";
const require = createRequire((process.env.PLAYWRIGHT_DIR || "/tmp/fxshots") + "/");
const { chromium } = require("playwright");

const url = process.argv[2];
const b = await chromium.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", args: ["--headless=new"] });
try {
  const p = await b.newPage({ locale: "fr-FR" });
  await p.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
  await p.waitForTimeout(2500);
  const text = await p.evaluate(() => document.body?.innerText || "");
  process.stdout.write(text.slice(0, 20000));
} catch (e) {
  process.stdout.write("");
} finally {
  await b.close();
}
