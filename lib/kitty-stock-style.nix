{ lib }:

let
  allowedTopLevelKeys = [
    "ansi"
    "ble"
    "completion"
    "palette"
    "schema"
    "terminal"
  ];

  allowedPaletteKeys = [
    "branch"
    "path"
    "staged"
    "unstaged"
    "untracked"
    "venv"
  ];

  allowedTerminalKeys = [
    "background"
    "cursor"
    "foreground"
    "selection_background"
    "selection_foreground"
  ];

  allowedAnsiKeys = map (i: "color${toString i}") (lib.range 0 15);

  allowedCompletionKeys = [
    "align_max"
    "align_min"
    "max_lines"
    "style"
  ];

  allowedStyles = [
    "align"
    "align-nowrap"
    "dense"
    "dense-nowrap"
    "desc-text"
    "linewise"
  ];

  # Audited against pinned ble.sh 0.4.0-devel3 (main + lib/core-complete).
  bleFaces = [
    "argument_error"
    "argument_option"
    "auto_complete"
    "command_alias"
    "command_builtin"
    "command_builtin_dot"
    "command_directory"
    "command_file"
    "command_function"
    "command_jobs"
    "command_keyword"
    "disabled"
    "filename_block"
    "filename_character"
    "filename_directory"
    "filename_directory_sticky"
    "filename_executable"
    "filename_link"
    "filename_ls_colors"
    "filename_orphan"
    "filename_other"
    "filename_pipe"
    "filename_setgid"
    "filename_setuid"
    "filename_socket"
    "filename_url"
    "filename_warning"
    "menu_filter_fixed"
    "menu_filter_input"
    "overwrite_mode"
    "prompt_status_line"
    "region"
    "region_insert"
    "region_match"
    "region_target"
    "syntax_brace"
    "syntax_command"
    "syntax_comment"
    "syntax_default"
    "syntax_delimiter"
    "syntax_document"
    "syntax_document_begin"
    "syntax_error"
    "syntax_escape"
    "syntax_expr"
    "syntax_function_name"
    "syntax_glob"
    "syntax_history_expansion"
    "syntax_param_expansion"
    "syntax_quotation"
    "syntax_quoted"
    "syntax_tilde"
    "syntax_varname"
    "varname_array"
    "varname_empty"
    "varname_export"
    "varname_expr"
    "varname_hash"
    "varname_number"
    "varname_readonly"
    "varname_transform"
    "varname_unset"
    "vbell"
    "vbell_erase"
    "vbell_flash"
  ];

  gflags = [
    "blink"
    "bold"
    "dim"
    "italic"
    "reverse"
    "strike"
    "underline"
  ];

  gcolors = [
    "black"
    "blue"
    "brown"
    "cyan"
    "gray"
    "green"
    "lime"
    "magenta"
    "orange"
    "pink"
    "red"
    "teal"
    "white"
    "yellow"
  ];

  isHexColor = s: builtins.isString s && builtins.match "#[0-9a-fA-F]{6}" s != null;

  validateGspec = face: spec:
    if !builtins.isString spec || spec == "" || builtins.stringLength spec > 140 then
      throw "malformed ble face: ${face}"
    else if spec == "none" then
      true
    else
      let
        parts = lib.splitString "," spec;
        step = acc: part:
          if builtins.elem part gflags then
            if builtins.elem part acc.seenFlags then
              throw "duplicate style flag: ${face}"
            else
              acc // { seenFlags = acc.seenFlags ++ [ part ]; }
          else
            let
              match = builtins.match "(fg|bg)=(.*)" part;
            in
            if match == null then
              throw "invalid ble face graphic spec: ${face}"
            else
              let
                which = builtins.elemAt match 0;
                color = builtins.elemAt match 1;
              in
              if builtins.elem which acc.seenProps then
                throw "duplicate face property: ${face}"
              else
                let
                  isHex = builtins.match "#[0-9a-fA-F]{6}" color != null;
                  isNamed = builtins.elem color gcolors;
                  numMatch = builtins.match "[0-9]{1,3}" color;
                  isNum = numMatch != null && (lib.toIntBase10 color <= 255);
                in
                if !(isHex || isNamed || isNum) then
                  throw "unknown color in ${face}: ${color}"
                else
                  acc // { seenProps = acc.seenProps ++ [ which ]; };
        finalAcc = builtins.foldl' step { seenFlags = []; seenProps = []; } parts;
      in
      finalAcc != null;

  validateToml = raw:
    if !builtins.isAttrs raw then
      throw "malformed TOML: root must be a table"
    else
      let
        unknownTop = builtins.filter (k: !builtins.elem k allowedTopLevelKeys) (builtins.attrNames raw);
      in
      if unknownTop != [] then
        throw "unknown Kitty key: ${builtins.head unknownTop}"
      else if !(raw ? schema) then
        throw "required schema missing"
      else if !builtins.isInt raw.schema || raw.schema != 1 then
        throw "malformed schema: schema must equal 1"
      else if !(raw ? palette) then
        throw "required missing table: palette"
      else if !builtins.isAttrs raw.palette then
        throw "scalar parent: palette"
      else if !(raw ? terminal) then
        throw "required missing table: terminal"
      else if !builtins.isAttrs raw.terminal then
        throw "scalar parent: terminal"
      else if !(raw ? completion) then
        throw "required missing table: completion"
      else if !builtins.isAttrs raw.completion then
        throw "scalar parent: completion"
      else if raw ? ansi && !builtins.isAttrs raw.ansi then
        throw "scalar parent: ansi"
      else if raw ? ble && !builtins.isAttrs raw.ble then
        throw "scalar parent: ble"
      else if raw ? ble && (builtins.filter (k: k != "faces") (builtins.attrNames raw.ble)) != [] then
        throw "only [ble.faces] is supported"
      else if raw ? ble && raw.ble ? faces && !builtins.isAttrs raw.ble.faces then
        throw "scalar parent: ble.faces"
      else
        let
          # Validate palette
          unknownPal = builtins.filter (k: !builtins.elem k allowedPaletteKeys) (builtins.attrNames raw.palette);
          _palUnknown = if unknownPal != [] then throw "unknown Kitty key: palette.${builtins.head unknownPal}" else null;
          _palColors = builtins.mapAttrs (k: v:
            if !isHexColor v then throw "malformed color: palette.${k}" else true
          ) raw.palette;

          # Validate terminal
          unknownTerm = builtins.filter (k: !builtins.elem k allowedTerminalKeys) (builtins.attrNames raw.terminal);
          _termUnknown = if unknownTerm != [] then throw "unknown Kitty key: terminal.${builtins.head unknownTerm}" else null;
          _termColors = builtins.mapAttrs (k: v:
            if !isHexColor v then throw "malformed color: terminal.${k}" else true
          ) raw.terminal;

          # Validate ansi if present
          unknownAnsi = if raw ? ansi then builtins.filter (k: !builtins.elem k allowedAnsiKeys) (builtins.attrNames raw.ansi) else [];
          _ansiUnknown = if unknownAnsi != [] then throw "unknown Kitty key: ansi.${builtins.head unknownAnsi}" else null;
          _ansiColors = if raw ? ansi then builtins.mapAttrs (k: v:
            if !isHexColor v then throw "malformed color: ansi.${k}" else true
          ) raw.ansi else {};

          # Validate completion
          unknownCmpl = builtins.filter (k: !builtins.elem k allowedCompletionKeys) (builtins.attrNames raw.completion);
          _cmplUnknown = if unknownCmpl != [] then throw "unknown Kitty key: completion.${builtins.head unknownCmpl}" else null;
          _cmplStyle =
            if raw.completion ? style then
              if !builtins.isString raw.completion.style || !builtins.elem raw.completion.style allowedStyles then
                throw "unsupported completion style"
              else true
            else true;
          _cmplAlignMin =
            if raw.completion ? align_min then
              let v = raw.completion.align_min; in
              if !builtins.isInt v || v < 1 || v > 150 then
                throw "completion width must be integer 1..150"
              else true
            else true;
          _cmplAlignMax =
            if raw.completion ? align_max then
              let v = raw.completion.align_max; in
              if !builtins.isInt v || v < 1 || v > 150 then
                throw "completion width must be integer 1..150"
              else true
            else true;
          _cmplMaxLines =
            if raw.completion ? max_lines then
              let v = raw.completion.max_lines; in
              if !builtins.isInt v || v < 1 || v > 150 then
                throw "completion width must be integer 1..150"
              else true
            else true;
          _cmplOrdering =
            if raw.completion ? align_min && raw.completion ? align_max then
              if raw.completion.align_min > raw.completion.align_max then
                throw "align_min > align_max"
              else true
            else true;

          # Validate ble.faces if present
          unknownFaces =
            if raw ? ble && raw.ble ? faces then
              builtins.filter (k: !builtins.elem k bleFaces) (builtins.attrNames raw.ble.faces)
            else [];
          _facesUnknown = if unknownFaces != [] then throw "unknown ble.sh face: ${builtins.head unknownFaces}" else null;
          _facesValid =
            if raw ? ble && raw.ble ? faces then
              builtins.mapAttrs (face: spec: validateGspec face spec) raw.ble.faces
            else {};
          validationTree = {
            inherit _palUnknown _palColors _termUnknown _termColors
              _ansiUnknown _ansiColors _cmplUnknown _cmplStyle
              _cmplAlignMin _cmplAlignMax _cmplMaxLines _cmplOrdering
              _facesUnknown _facesValid;
          };
        in
        builtins.deepSeq validationTree raw;

  mergeStyles = { sample, projected }:
    let
      mergedPalette = sample.palette // (projected.palette or {});
      mergedTerminal = sample.terminal // (projected.terminal or {});
      mergedCompletion = sample.completion // (projected.completion or {});
      mergedAnsi = (sample.ansi or {}) // (projected.ansi or {});
      mergedBleFaces = (sample.ble.faces or {}) // (if projected ? ble && projected.ble ? faces then projected.ble.faces else {});
    in
    if mergedCompletion.align_min > mergedCompletion.align_max then
      throw "align_min > align_max"
    else
    {
      schema = 1;
      palette = mergedPalette;
      terminal = mergedTerminal;
      completion = mergedCompletion;
      ansi = mergedAnsi;
      ble = {
        faces = mergedBleFaces;
      };
    };

  renderToml = style:
    let
      lines = [
        "# DINAS stock Kitty fallback style - generated by Nix"
        "schema = 1"
        ""
        "[palette]"
      ]
      ++ (map (k: "${k} = \"${style.palette.${k}}\"") (builtins.sort builtins.lessThan (builtins.attrNames style.palette)))
      ++ [
        ""
        "[terminal]"
      ]
      ++ (map (k: "${k} = \"${style.terminal.${k}}\"") (builtins.sort builtins.lessThan (builtins.attrNames style.terminal)))
      ++ (lib.optionals (style ? ansi && style.ansi != {}) ([
        ""
        "[ansi]"
      ] ++ (map (k: "${k} = \"${style.ansi.${k}}\"") (builtins.sort (a: b: (lib.toInt (lib.removePrefix "color" a)) < (lib.toInt (lib.removePrefix "color" b))) (builtins.attrNames style.ansi)))))
      ++ [
        ""
        "[completion]"
      ]
      ++ (map (k:
        let v = style.completion.${k}; in
        if builtins.isInt v then "${k} = ${toString v}" else "${k} = \"${v}\""
      ) (builtins.sort builtins.lessThan (builtins.attrNames style.completion)))
      ++ (lib.optionals (style ? ble && style.ble ? faces && style.ble.faces != {}) ([
        ""
        "[ble.faces]"
      ] ++ (map (k: "${k} = \"${style.ble.faces.${k}}\"") (builtins.sort builtins.lessThan (builtins.attrNames style.ble.faces)))))
      ++ [ "" ];
    in
    builtins.concatStringsSep "\n" lines;

  loadAndMergeStyle = { localConfig, samplePath ? ../config/kitty/stock-bash-style.toml }:
    let
      configDir = localConfig.outPath or localConfig;
      tomlPath = (toString configDir) + "/kitty-bash.toml";
      sample = validateToml (builtins.fromTOML (builtins.readFile samplePath));
      # Other admitted TOML belongs to its own consumer; projection owns the
      # aggregate filename boundary. Missing Kitty data uses the admitted file.
      projected = validateToml (builtins.fromTOML (builtins.readFile (
        if builtins.pathExists tomlPath then tomlPath
        else ../config/nix-darwin/default/kitty-bash.toml
      )));
    in
    mergeStyles { inherit sample projected; };

in
{
  inherit
    allowedTopLevelKeys
    allowedPaletteKeys
    allowedTerminalKeys
    allowedAnsiKeys
    allowedCompletionKeys
    allowedStyles
    bleFaces
    gflags
    gcolors
    isHexColor
    validateGspec
    validateToml
    mergeStyles
    renderToml
    loadAndMergeStyle;
}
