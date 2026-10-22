{
  lib,
  fetchPypi,
  python3Packages,
  termcolor,
}:

python3Packages.buildPythonPackage rec {
  pname = "pypistats";
  version = "1.14.0";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-0Mpookl+N8e3QWKJ8mKjuUER5jf4OxzaHUkxx6tLQmw=";
  };

  nativeBuildInputs = [
    python3Packages.hatchling
    python3Packages.hatch-vcs
  ];

  dependencies = with python3Packages; [
    platformdirs
    prettytable
    python-slugify
    urllib3
  ] ++ [ termcolor ];

  pythonImportsCheck = [
    "pypistats"
  ];

  doCheck = false;

  meta = with lib; {
    description = "Python interface to PyPI Stats API";
    homepage = "https://github.com/hugovk/pypistats";
    license = licenses.mit;
    maintainers = [ ];
  };
}
