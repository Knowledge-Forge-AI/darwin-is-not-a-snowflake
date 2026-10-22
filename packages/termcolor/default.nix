{
  lib,
  fetchPypi,
  python3Packages,
}:

python3Packages.buildPythonPackage rec {
  pname = "termcolor";
  version = "3.2.0";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-YQ5kVv7sQsS80ok0qMh6BsP6KLAVYdRqoJqYgbhiLFg=";
  };

  nativeBuildInputs = [
    python3Packages.hatchling
    python3Packages.hatch-vcs
  ];

  pythonImportsCheck = [ "termcolor" ];
  doCheck = false;

  meta = with lib; {
    description = "ANSI color formatting for terminal output";
    homepage = "https://github.com/termcolor/termcolor";
    license = licenses.mit;
    maintainers = [ ];
  };
}
