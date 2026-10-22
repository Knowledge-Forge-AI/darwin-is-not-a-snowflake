{
  fetchurl,
  lib,
  python3Packages,
  src,
}:

let
  pythonLibDir = lib.removeSuffix "/site-packages" python3Packages.python.sitePackages;
  setuptools = python3Packages.buildPythonPackage rec {
    pname = "setuptools";
    version = "83.0.0";
    format = "wheel";

    src = fetchurl {
      url = "https://files.pythonhosted.org/packages/5d/40/e1e72872c6354b306daef1703549e8e83b4d43cfea356311bf722a043752/setuptools-83.0.0-py3-none-any.whl";
      hash = "sha256-KbI8Ng8i9BTccza7OReMx7y/YCHtJzPN4XPwnboZq7M=";
    };

    doCheck = false;
    pythonImportsCheck = [ "setuptools" ];
  };
  typingExtensions = python3Packages.typing-extensions.overridePythonAttrs (_oldAttrs: rec {
    version = "4.16.0";
    src = python3Packages.fetchPypi {
      pname = "typing_extensions";
      inherit version;
      hash = "sha256-3Jg9GaUJyU26ci7mq9M5QPfAWoniQ8R+kH60228aQ+U=";
    };
  });
  psycopg = python3Packages.psycopg.override {
    typing-extensions = typingExtensions;
  };
in
python3Packages.buildPythonApplication {
  pname = "repomap-kg";
  version = "0.1.0";
  pyproject = true;

  inherit src;

  build-system = [
    setuptools
  ];

  dependencies = [
    psycopg
    setuptools
    typingExtensions
  ];

  postInstall = ''
    resources_target="$out/${pythonLibDir}/resources"
    install -d "$resources_target"
    cp -R src/main/resources/rdbms "$resources_target/rdbms"

    runtime_source_target="$out/lib"
    install -d "$runtime_source_target/src/main"
    install -m 0444 pyproject.toml README.md "$runtime_source_target/"
    cp -R src/main/go "$runtime_source_target/src/main/go"
    cp -R src/main/python "$runtime_source_target/src/main/python"
    cp -R src/main/resources "$runtime_source_target/src/main/resources"
    find "$runtime_source_target/src/main/python" -type d -name __pycache__ -prune -exec rm -r {} +

    test -f "$runtime_source_target/src/main/python/repomap_kg/__main__.py"
    test -d "$runtime_source_target/src/main/resources"
    test -f "$runtime_source_target/src/main/go/go.mod"
  '';

  pythonImportsCheck = [
    "psycopg"
    "repomap_kg"
    "setuptools"
    "typing_extensions"
  ];

  meta = {
    description = "Deterministic knowledge graph builder for polyglot repositories";
    homepage = "https://github.com/Knowledge-Forge-AI/repo-map";
    license = lib.licenses.agpl3Plus;
    mainProgram = "repomap-kg";
    platforms = lib.platforms.unix;
  };
}
