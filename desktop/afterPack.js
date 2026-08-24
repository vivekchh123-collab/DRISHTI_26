/* Stamp the packaged executable with our icon and version strings.
 *
 * electron-builder would normally do this itself, but doing so pulls in its
 * winCodeSign helper, whose archive contains macOS symlinks that Windows
 * refuses to create without Developer Mode or an elevated shell. Requiring
 * either to produce an unsigned internal build is a poor trade, so
 * `signAndEditExecutable` is off and the one part we actually wanted —
 * branding — is done here directly with rcedit.
 *
 * If rcedit cannot be found the build continues with the stock Electron icon
 * rather than failing: a slightly wrong icon is a far better outcome than no
 * installer.
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

function findRcedit() {
  const roots = [
    path.join(process.env.LOCALAPPDATA || "",
              "electron-builder", "Cache", "winCodeSign"),
  ];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const entry of fs.readdirSync(root)) {
      const candidate = path.join(root, entry, "rcedit-x64.exe");
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return null;
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "win32") return;

  const exe = path.join(context.appOutDir, "DRISHTI.exe");
  const ico = path.join(__dirname, "build", "icon.ico");
  const rcedit = findRcedit();

  if (!rcedit || !fs.existsSync(exe) || !fs.existsSync(ico)) {
    console.log("  • afterPack: rcedit unavailable, keeping the stock icon");
    return;
  }

  const version = require("./package.json").version;
  try {
    execFileSync(rcedit, [
      exe,
      "--set-icon", ico,
      "--set-version-string", "ProductName", "DRISHTI",
      "--set-version-string", "FileDescription",
      "DRISHTI - Multi-Hazard Red Zones and Relocation Planning",
      "--set-version-string", "CompanyName", "DRISHTI",
      "--set-version-string", "LegalCopyright", "Apache-2.0",
      "--set-file-version", version,
      "--set-product-version", version,
    ], { stdio: "inherit" });
    console.log("  • afterPack: icon and version stamped onto DRISHTI.exe");
  } catch (err) {
    console.log("  • afterPack: rcedit failed, keeping the stock icon:", err.message);
  }
};
