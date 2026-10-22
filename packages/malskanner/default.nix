{ pkgs }:

# Local prompt-injection scanner CLI. The wrapper package pins malskanner to an
# exact version; package-lock.json pins every transitive tarball by integrity.
# See docs/architecture/source-audit-tools.md.
pkgs.buildNpmPackage {
  pname = "malskanner";
  version = "0.1.3";
  nodejs = pkgs.nodejs_22;
  src = pkgs.lib.fileset.toSource {
    root = ./.;
    fileset = pkgs.lib.fileset.unions [
      ./package.json
      ./package-lock.json
    ];
  };
  npmDepsHash = "sha256-pzpUzk19r/A5/56/wvZKoq1fbfBu87Ocib5m3RHodzQ=";
  # No dependency lifecycle scripts, audit or funding requests in the build.
  npmFlags = [
    "--ignore-scripts"
    "--no-audit"
    "--no-fund"
  ];
  dontNpmBuild = true;
  nativeBuildInputs = [ pkgs.makeWrapper ];
  postInstall = ''
    makeWrapper ${pkgs.nodejs_22}/bin/node $out/bin/malskanner \
      --add-flags $out/lib/node_modules/dinas-malskanner/node_modules/malskanner/dist/cli.js
  '';
  meta = {
    description = "Scan a repository for hidden prompt-injection payloads";
    homepage = "https://github.com/octolabo/malskanner";
    license = pkgs.lib.licenses.mit;
    mainProgram = "malskanner";
    platforms = pkgs.lib.platforms.darwin;
  };
}
