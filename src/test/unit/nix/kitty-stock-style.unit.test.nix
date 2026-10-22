# nix-darwin-test-source-target: lib/kitty-stock-style.nix
# nix-darwin-test-source-target: modules/kitty.nix
# nix-darwin-test-source-target: flake.nix
{ repoRoot, system, lib ? (builtins.getFlake "path:${toString repoRoot}").legacyPackages.${system}.lib }:

let
  styleLib = import (repoRoot + "/lib/kitty-stock-style.nix") { inherit lib; };
  sample = builtins.fromTOML (builtins.readFile (repoRoot + "/config/kitty/stock-bash-style.toml"));
  throws = expr: !(builtins.tryEval expr).success;
  # Evaluate the actual host constructor without fetching/instantiating a flake.
  # darwinSystem captures its module arguments; this is not locked host evaluation.
  sourceOutputs = (import (repoRoot + "/flake.nix")).outputs {
    self = {};
    nixpkgs = { inherit lib; rev = "fixture"; outPath = repoRoot; };
    nixpkgs-unstable.legacyPackages.${system} = {};
    nix-darwin.lib.darwinSystem = args: args;
    localConfig = repoRoot + "/config/nix-darwin/default";
  };
  hostPolicy = name:
    let modules = (sourceOutputs.lib.mkDarwinConfiguration { hostName = name; }).modules;
    in (builtins.elemAt modules 1) { config = {}; pkgs = {}; };
  kittyModule = (import (repoRoot + "/modules/kitty.nix") { userName = "fixture"; }) {
    inherit lib;
    config = {};
    pkgs = {};
  };
in
{
  testActualSourceHostSelectionAndAdmission = {
    expr = {
      target = (hostPolicy "example-primary").dinas.kittyStockBash.enable;
      laptop = (hostPolicy "example-workstation").dinas.kittyStockBash.enable;
      targetStockBrowsers = (hostPolicy "example-primary").programs.browserBaseline.stockApplications;
      laptopStockBrowsers = (hostPolicy "example-workstation").programs.browserBaseline.stockApplications;
      r3 = sourceOutputs.lib.browserRolesR3Selected;
      allowlist = sourceOutputs.configAllowlist;
      defaultDirenv = kittyModule.options.dinas.kittyStockBash.direnv.enable.default;
      independentDirenvOptIn = (lib.evalModules { modules = [
        { options.dinas.kittyStockBash = kittyModule.options.dinas.kittyStockBash; }
        { dinas.kittyStockBash.direnv.enable = true; }
      ]; }).config.dinas.kittyStockBash.direnv.enable;
    };
    expected = {
      target = true;
      laptop = false;
      targetStockBrowsers = true;
      laptopStockBrowsers = false;
      r3 = false;
      allowlist."kitty-bash.toml" = [ "schema" "palette" "terminal" "completion" "ansi?" "ble.faces?" ];
      defaultDirenv = false;
      independentDirenvOptIn = true;
    };
  };

  testStockStyleLoadsCommittedSampleDefault = {
    expr =
      let
        loaded = styleLib.loadAndMergeStyle {
          localConfig = repoRoot + "/config/nix-darwin/default";
          samplePath = repoRoot + "/config/kitty/stock-bash-style.toml";
        };
      in
      {
        schema = loaded.schema;
        path = loaded.palette.path;
        branch = loaded.palette.branch;
        foreground = loaded.terminal.foreground;
        background = loaded.terminal.background;
        completionStyle = loaded.completion.style;
        alignMin = loaded.completion.align_min;
        alignMax = loaded.completion.align_max;
        maxLines = loaded.completion.max_lines;
        autoCompleteFace = loaded.ble.faces.auto_complete;
      };
    expected = {
      schema = 1;
      path = "#ffd700";
      branch = "#5fd7ff";
      foreground = "#33ff00";
      background = "#000000";
      completionStyle = "align-nowrap";
      alignMin = 4;
      alignMax = 64;
      maxLines = 12;
      autoCompleteFace = "fg=#89939f,italic";
    };
  };

  testStockStyleRejectsFacesAbsentFromPinnedBlesh = {
    expr = map (face: throws (styleLib.validateToml {
      schema = 1; palette = {}; terminal = {}; completion = {};
      ble.faces.${face} = "fg=#123456";
    })) [
      "menu_complete_match" "menu_complete_selected" "menu_desc_default"
      "menu_desc_quote" "menu_desc_type" "command_suffix" "command_suffix_new" "varname_new"
    ];
    expected = [ true true true true true true true true ];
  };

  testStockStyleMergingPartialValuesOverridesSample = {
    expr =
      let
        merged = styleLib.mergeStyles {
          inherit sample;
          projected = {
            schema = 1;
            palette = {
              path = "#123456";
            };
            terminal = {
              foreground = "#abcdef";
            };
            completion = {
              max_lines = 20;
            };
          };
        };
      in
      {
        path = merged.palette.path;
        branch = merged.palette.branch;
        foreground = merged.terminal.foreground;
        background = merged.terminal.background;
        maxLines = merged.completion.max_lines;
        alignMin = merged.completion.align_min;
      };
    expected = {
      path = "#123456";
      branch = "#5fd7ff";
      foreground = "#abcdef";
      background = "#000000";
      maxLines = 20;
      alignMin = 4;
    };
  };

  testStockStyleRendersDeterministicToml = {
    expr =
      let
        rendered = styleLib.renderToml sample;
      in
      {
        hasHeader = lib.hasPrefix "# DINAS stock Kitty fallback style - generated by Nix\nschema = 1" rendered;
        hasPalette = lib.hasInfix "\n[palette]\nbranch = \"#5fd7ff\"" rendered;
        hasTerminal = lib.hasInfix "\n[terminal]\nbackground = \"#000000\"" rendered;
        hasCompletion = lib.hasInfix "\n[completion]\nalign_max = 64\nalign_min = 4" rendered;
        hasBleFaces = lib.hasInfix "\n[ble.faces]\nauto_complete = \"fg=#89939f,italic\"" rendered;
      };
    expected = {
      hasHeader = true;
      hasPalette = true;
      hasTerminal = true;
      hasCompletion = true;
      hasBleFaces = true;
    };
  };

  testStockStyleRejectsRequiredMissingTables = {
    expr = {
      missingSchema = throws (styleLib.validateToml { palette = {}; terminal = {}; completion = {}; });
      schemaNotOne = throws (styleLib.validateToml { schema = 2; palette = {}; terminal = {}; completion = {}; });
      schemaString = throws (styleLib.validateToml { schema = "1"; palette = {}; terminal = {}; completion = {}; });
      missingPalette = throws (styleLib.validateToml { schema = 1; terminal = {}; completion = {}; });
      missingTerminal = throws (styleLib.validateToml { schema = 1; palette = {}; completion = {}; });
      missingCompletion = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; });
    };
    expected = {
      missingSchema = true;
      schemaNotOne = true;
      schemaString = true;
      missingPalette = true;
      missingTerminal = true;
      missingCompletion = true;
    };
  };

  testStockStyleRejectsScalarParents = {
    expr = {
      paletteScalar = throws (styleLib.validateToml { schema = 1; palette = "scalar"; terminal = {}; completion = {}; });
      terminalScalar = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = 123; completion = {}; });
      completionScalar = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = true; });
      ansiScalar = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ansi = "scalar"; });
      bleScalar = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = "scalar"; });
      bleFacesScalar = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = 123; }; });
    };
    expected = {
      paletteScalar = true;
      terminalScalar = true;
      completionScalar = true;
      ansiScalar = true;
      bleScalar = true;
      bleFacesScalar = true;
    };
  };

  testStockStyleRejectsUnknownKittyKeys = {
    expr = {
      unknownTop = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; unknown = 1; });
      unknownPalette = throws (styleLib.validateToml { schema = 1; palette = { unknown = "#123456"; }; terminal = {}; completion = {}; });
      unknownTerminal = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = { unknown = "#123456"; }; completion = {}; });
      unknownCompletion = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = { unknown = 1; }; });
      unknownAnsi = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ansi = { unknown = "#123456"; }; });
      unknownBleSubtable = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { other = {}; }; });
      unknownBleFace = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = { unknown_face = "bold"; }; }; });
    };
    expected = {
      unknownTop = true;
      unknownPalette = true;
      unknownTerminal = true;
      unknownCompletion = true;
      unknownAnsi = true;
      unknownBleSubtable = true;
      unknownBleFace = true;
    };
  };

  testStockStyleRejectsMalformedValues = {
    expr = {
      paletteBadHex = throws (styleLib.validateToml { schema = 1; palette = { path = "not-hex"; }; terminal = {}; completion = {}; });
      terminalBadHex = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = { foreground = "#12345"; }; completion = {}; });
      ansiBadHex = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ansi = { color0 = "blue"; }; });
      completionBadStyle = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = { style = "invalid"; }; });
      completionAlignMinZero = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = { align_min = 0; }; });
      completionAlignMinHigh = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = { align_min = 151; }; });
      completionAlignMinGreaterThanMax = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = { align_min = 50; align_max = 10; }; });
      bleFaceEmpty = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = { auto_complete = ""; }; }; });
      bleFaceTooLong = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = { auto_complete = lib.fixedWidthString 141 "a" ""; }; }; });
      bleFaceDuplicateFlag = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = { auto_complete = "bold,bold"; }; }; });
      bleFaceDuplicateProp = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = { auto_complete = "fg=red,fg=blue"; }; }; });
      bleFaceUnknownColor = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = { auto_complete = "fg=unknown"; }; }; });
      bleFaceSyntaxError = throws (styleLib.validateToml { schema = 1; palette = {}; terminal = {}; completion = {}; ble = { faces = { auto_complete = "invalidpart"; }; }; });
    };
    expected = {
      paletteBadHex = true;
      terminalBadHex = true;
      ansiBadHex = true;
      completionBadStyle = true;
      completionAlignMinZero = true;
      completionAlignMinHigh = true;
      completionAlignMinGreaterThanMax = true;
      bleFaceEmpty = true;
      bleFaceTooLong = true;
      bleFaceDuplicateFlag = true;
      bleFaceDuplicateProp = true;
      bleFaceUnknownColor = true;
      bleFaceSyntaxError = true;
    };
  };

  testStockStyleAdmittedAbsentFileFallsBackToSample = {
    expr =
      let
        loaded = styleLib.loadAndMergeStyle {
          localConfig = repoRoot + "/lib"; # contains no kitty-bash.toml
          samplePath = repoRoot + "/config/kitty/stock-bash-style.toml";
        };
      in
      {
        path = loaded.palette.path;
        foreground = loaded.terminal.foreground;
        schema = loaded.schema;
      };
    expected = {
      path = "#ffd700";
      foreground = "#33ff00";
      schema = 1;
    };
  };
}
