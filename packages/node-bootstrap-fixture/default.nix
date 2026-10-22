{ pkgs }:

pkgs.buildNpmPackage {
  pname = "dinas-node-bootstrap-fixture";
  version = "1.0.0";
  src = ../../src/test/fixtures/node-bootstrap;
  npmDepsHash = "sha256-bO+h/BR56yqsQcTBmc1+gKgiKKtZngc65q1EKr68uXk=";
  npmBuildScript = "verify";
  installPhase = ''
    mkdir -p $out/share/dinas-node-bootstrap-fixture
    cp local-tools.json $out/share/dinas-node-bootstrap-fixture/
  '';
}
