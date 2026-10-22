{ pkgs }:

pkgs.buildNpmPackage {
  pname = "dinas-frontend-tools";
  version = "1.0.0";
  src = ./.;
  npmDepsHash = "sha256-nmli1DIDcFRfd7zvVwhbGDfizYeQyM/hHLAkigV0ouI=";
  dontNpmBuild = true;
  postInstall = ''
    mkdir -p $out/bin
    ln -s $out/lib/node_modules/dinas-frontend-tools/node_modules/vite/bin/vite.js $out/bin/vite
    ln -s $out/lib/node_modules/dinas-frontend-tools/node_modules/vitest/vitest.mjs $out/bin/vitest
    chmod +x $out/bin/vite $out/bin/vitest
  '';
}
