{ lib }:

let
  pathKeys = [
    "ai_index_root"
    "dinas_root"
    "nbin_dev_root"
    "nbin_root"
    "schema"
    "workspace_root"
  ];
  resolvePath = userHome: value:
    if lib.hasPrefix "$HOME/" value then
      "${userHome}/${lib.removePrefix "$HOME/" value}"
    else if lib.hasPrefix "/" value then
      value
    else
      throw "DINAS path projection contains a non-absolute path";

  loadProjection = { projection, userHome }:
    let
      pathsFile = "${projection.outPath}/paths.json";
      loaded = builtins.fromJSON (builtins.readFile pathsFile);
    in
    assert builtins.attrNames loaded == pathKeys;
    assert loaded.schema == "dinas-paths-v1";
    assert builtins.all builtins.isString (builtins.attrValues loaded);
    {
      schema = loaded.schema;
      workspace_root = resolvePath userHome loaded.workspace_root;
      nbin_root = resolvePath userHome loaded.nbin_root;
      nbin_dev_root = resolvePath userHome loaded.nbin_dev_root;
      ai_index_root = resolvePath userHome loaded.ai_index_root;
      dinas_root = resolvePath userHome loaded.dinas_root;
    };
in
{
  inherit loadProjection;
}
