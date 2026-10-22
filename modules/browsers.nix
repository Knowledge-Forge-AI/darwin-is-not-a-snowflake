{ config, options, lib, pkgs, ... }:

let
  legacyChromePolicyFile = ../config/browser/chrome-policy-legacy.plist;
  legacyChromePolicyDefaults = import ../config/browser/chrome-policy-legacy-defaults.nix;
  coreChromePolicyDefaults = import ../config/browser/chrome-policy-defaults.nix;
  coreOwnership = builtins.fromJSON (builtins.readFile ../config/browser/core-ownership.json);
  protonDelivery = import ../config/browser/proton-vpn-r3r3.nix;
  extensionOwnership = import ../lib/browser-extension-ownership.nix { inherit lib; };
  roleOwnership = builtins.fromJSON (builtins.readFile ../config/browser/roles-r3.json);
  coreWriterFile = toString ./browsers.nix;
  # Resolve the framework aggregate from option declaration provenance, not
  # modulesPath (which need not equal the module's recorded source location).
  frameworkActivationFiles = builtins.filter
    (file: lib.hasSuffix "/system/activation-scripts.nix" file)
    (options.system.activationScripts.declarations or []);
  legacyClearNewTabExternalExtension = ../config/browser/chrome-clear-new-tab-external-extension.json;
  legacyFirefoxPolicies = ../config/browser/firefox-policies.json;
  legacyKeepassxcChromeNativeMessaging = ../config/browser/keepassxc-chrome-native-messaging.json;
  legacyKeepassxcFirefoxNativeMessaging = ../config/browser/keepassxc-firefox-native-messaging.json;

  cfg = config.programs.browserBaseline;
  options_ = options.programs.browserBaseline;
  # Acquisition alone never selects policy writers, profile helpers or R3 holds.
  # Preserve the existing selectors' acquisition conditions for direct consumers.
  stockBraveAndLibreWolf = cfg.stockApplications || cfg.corePolicyOwnership;
  stockOrion = cfg.stockApplications || cfg.rolesR3;
  # Keep the CLI while the official cask owns the writable, self-updating app.
  # This output contains only bin/brave; pkgs.brave remains YouTube's source.
  stockBraveCLI = pkgs.writeShellScriptBin "brave" ''
    exec '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser' "$@"
  '';
  extensionDelivery = extensionOwnership.merge
    (protonDelivery.contributions ++ cfg.extensionContributions);
  extensionDeliveryViolations = lib.optionals cfg.rolesR3 (
    map (message: { type = "extension-semantic-ownership";
      option = "programs.browserBaseline.extensionContributions"; inherit message;
    }) extensionDelivery.errors
    ++ lib.optionals (options_.extensionContributions.highestPrio != 1500)
      (checkPriorityViolations options_.extensionContributions)
  );

  # Release requires a subsequent reviewed source change, not an option override
  # or a kit checkbox. The proposed uBOL-only policy can uninstall formerly forced
  # extensions, and the packaged wallpaper helpers still depend on Clear New Tab.
  coreDeploymentHeld = cfg.corePolicyOwnership || cfg.rolesR3;
  coreDeploymentHoldMessage = "R3 browser activation held: keep the candidate inactive for unrelated deployments; qualify the preserving Chrome extension/storage and KeePassXC transition, preserve Clear New Tab wallpaper dependencies, evaluate the native composed graph and unique writers, and qualify R3 packages, isolated Brave roles and Apple Passwords compatibility before a reviewed source release.";
  coreDeploymentGuard = lib.optionalString coreDeploymentHeld ''
    printf '%s\n' ${lib.escapeShellArg coreDeploymentHoldMessage} >&2
    exit 78
  '';
  # A canonical stock executable and a fresh-profile adapter; no environment bypass.
  # Even the isolated entrypoint is mechanically held until native qualification
  # and a subsequent reviewed source release cover inherited policy semantics.
  automationBrowser = pkgs.writeShellScriptBin "browser-automation" ''
    printf '%s\n' 'R3_AUTOMATION_HELD_NATIVE_QUALIFICATION_AND_SOURCE_RELEASE_REQUIRED' >&2
    exit 78
    exec ${pkgs.python3}/bin/python3 ${../libexec/browser-automation/launch.py} \
      '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser' "$@"
  '';
  coreChromeRecommendedPreflight = lib.optionalString cfg.corePolicyOwnership ''
    recommendedChromePolicy="/Library/Preferences/com.google.Chrome.plist"
    if [ -L "$recommendedChromePolicy" ] || { [ -e "$recommendedChromePolicy" ] && ! cmp -s "${legacyChromePolicyFile}" "$recommendedChromePolicy"; }; then
      printf '%s\n' 'Chrome recommended policy has unqualified provenance; preserve it and hold deployment.' >&2
      exit 78
    fi
  '';

  optChromePreferences = options_.chrome.managedPreferences;
  optChromeExtensions = options_.chrome.externalExtensions;
  optChromeNMH = options_.chrome.nativeMessagingHosts;
  optSafariPreferences = options_.safari.managedPreferences;
  optSafariExtensions = options_.safari.requiredExtensions;
  optFirefoxPolicies = options_.firefox.policies;
  optFirefoxPoliciesPayload = options_.firefox.policiesJsonPayload;
  optFirefoxPolicyDelivery = options_.firefox.policyDelivery;
  optFirefoxNMH = options_.firefox.nativeMessagingHosts;
  optVivaldiPreferences = options_.vivaldi.managedPreferences;
  optVivaldiExtensions = options_.vivaldi.externalExtensions;
  optVivaldiNMH = options_.vivaldi.nativeMessagingHosts;
  optFloorpPolicies = options_.floorp.policies;

  # Architecture and Seam Representation Rationale (Roadmap BUX2 / §3 & §5):
  # - Option 2 Payload Model: For Firefox policies and external manifests, BUX2 provides
  #   both generic structured Nix attrsets (e.g. firefox.policies, structured records)
  #   and immutable JSON payload files (firefox.policiesJsonPayload, path inputs) to guarantee
  #   exact byte parity with baseline files without sacrificing structured key validation.
  # - Physical Writer Ownership: modules/browsers.nix is the sole physical writer of root
  #   browser paths. Destination modules contribute data only.
  # - Duplicate Leaf Granularity: Collision detection operates at the top-level policy leaf
  #   (e.g. BlockThirdPartyCookies, ExtensionSettings). Defining the same top-level key across
  #   modules—even with identical values—is strictly rejected. Sub-key merging for complex
  #   dictionaries like ExtensionSettings is deferred to BUX3.
  # - Priority Wrapper Invariant (§4.2): Seam definitions must carry default priority (100).
  #   Wrappers with _type == "override" (mkForce, mkOverride, mkDefault, mkOptionDefault)
  #   are rejected at both the option level and nested definition/key levels.
  # - Order Wrappers Assessment (§4.2): mkBefore / mkAfter (_type == "order") do not bypass
  #   duplicate detection because top-level keys remain visible in raw definition attrsets.
  # - Priority Shadowing Boundary: Under Nix's lib.modules.filterOverrides, if Module A uses
  #   mkDefault and Module B uses default priority (100) on the same option, Nix prunes Module A
  #   prior to definition inspection. Module B wins.
  # - Bounded Cleanup Ownership (§6): Host cleanup of absent contributions is strictly bounded
  #   to known seam-managed identities (knownExternalExtensionIdentities and knownNativeMessagingHosts).
  #   Symlinks are checked against store/legacy paths before unlinking. Dynamic destination-declared
  #   identity registries are deferred to BUX3.

  # Known seam-managed identities for bounded cleanup
  knownExternalExtensionIdentities = [
    "felphkbfjadmcejnibcmcncimlappdde"
  ];
  knownNativeMessagingHosts = [
    "org.keepassxc.keepassxc_browser"
  ];

  # Helper to recognize Nix override wrappers without false-positives on legitimate policy
  # attributes that happen to be named "priority".
  isOverrideWrapper = v:
    builtins.isAttrs v && (v._type or "" == "override");

  # Guard violation detection
  # 1. Non-default priority detection:
  #    - Whole option priority check (when defined, highestPrio must be 100)
  #    - Definition-level override check (scanning raw definitions for _type == "override")
  #    - Nested key override check (scanning attrs for per-key override wrappers)
  checkPriorityViolations = opt:
    let
      wholeOptionViolation =
        if opt.isDefined && opt.highestPrio != 100 then
          [{
            type = "priority-override";
            option = lib.showOption opt.loc;
            loc = opt.loc;
            priority = opt.highestPrio;
            message = "Option ${lib.showOption opt.loc} defined with non-default priority ${toString opt.highestPrio}";
          }]
        else [];

      defs = opt.definitionsWithLocations or [];

      nestedViolations = lib.concatMap (d:
        if builtins.isAttrs d.value then
          (if isOverrideWrapper d.value then
            [{
              type = "priority-override";
              option = lib.showOption opt.loc;
              loc = opt.loc;
              file = d.file;
              priority = d.value.priority or 100;
              message = "Option ${lib.showOption opt.loc} defined with override wrapper in ${d.file}";
            }]
          else [])
          ++
          (lib.concatMap (k:
            let v = d.value.${k}; in
            if isOverrideWrapper v then
              [{
                type = "priority-override";
                option = lib.showOption (opt.loc ++ [ k ]);
                loc = opt.loc ++ [ k ];
                key = k;
                file = d.file;
                priority = v.priority or 100;
                message = "Key ${lib.showOption (opt.loc ++ [ k ])} defined with override priority in ${d.file}";
              }]
            else []
          ) (builtins.attrNames d.value))
        else []
      ) defs;
    in wholeOptionViolation ++ nestedViolations;

  # Priority check for selector options that carry an option-declaration default (priority 1500).
  # Defends against mkForce, mkOverride, mkDefault, etc. while tolerating unset option default.
  checkSelectorPriorityViolations = opt:
    let
      nonDefaultPriority =
        opt.isDefined && opt.highestPrio != 100 && opt.highestPrio != 1500;
      optionDefaultOverride =
        opt.isDefined && opt.highestPrio == 1500 && (
          builtins.length (opt.definitionsWithLocations or []) > 1
          || builtins.any (f: f != "<unknown-file>" && !(builtins.elem f (opt.declarations or []))) (opt.files or [])
        );
    in
      if nonDefaultPriority || optionDefaultOverride then
        [{
          type = "priority-override";
          option = lib.showOption opt.loc;
          loc = opt.loc;
          priority = opt.highestPrio;
          message = "Option ${lib.showOption opt.loc} defined with non-default priority ${toString opt.highestPrio}";
        }]
      else [];

  # 2. Duplicate key detection:
  #    - Across modules, each leaf key must have at most 1 definition.
  #    - Identical values across multiple modules remain duplicate collisions.
  checkDuplicateKeyViolations = opt:
    let
      defs = opt.definitionsWithLocations or [];
      keysWithFiles = lib.foldl (acc: d:
        if builtins.isAttrs d.value then
          lib.foldl (accInner: k:
            accInner // { ${k} = (accInner.${k} or []) ++ [ d.file ]; }
          ) acc (builtins.attrNames d.value)
        else acc
      ) {} defs;
    in
      lib.concatMap (k:
        let files = keysWithFiles.${k}; in
        if builtins.length files > 1 then
          [{
            type = "duplicate-key";
            option = lib.showOption opt.loc;
            loc = opt.loc ++ [ k ];
            key = k;
            files = files;
            message = "Duplicate definition for seam key ${lib.showOption (opt.loc ++ [ k ])} in files: ${builtins.concatStringsSep ", " files}";
          }]
        else []
      ) (builtins.attrNames keysWithFiles);

  checkDuplicatePayloadViolations = opt:
    let defs = opt.definitionsWithLocations or []; in
    if builtins.length defs > 1 then
      [{
        type = "duplicate-key";
        option = lib.showOption opt.loc;
        loc = opt.loc;
        files = map (d: d.file) defs;
        message = "Duplicate definition for seam payload ${lib.showOption opt.loc} in files: ${builtins.concatStringsSep ", " (map (d: d.file) defs)}";
      }]
    else [];

  # 3. Identifier format validation:
  #    Ensures extension IDs and host names match strict safe character sets,
  #    preventing shell escaping or path traversal when interpolated into activation scripts.
  checkKeyFormatViolations = opt: pattern: desc:
    let defs = opt.definitionsWithLocations or []; in
    lib.concatMap (d:
      if builtins.isAttrs d.value then
        lib.concatMap (k:
          if builtins.match pattern k == null then
            [{
              type = "invalid-identifier";
              option = lib.showOption (opt.loc ++ [ k ]);
              loc = opt.loc ++ [ k ];
              key = k;
              file = d.file;
              message = "Invalid ${desc} '${k}' in ${d.file}. Identifier must match pattern '${pattern}'.";
            }]
          else []
        ) (builtins.attrNames d.value)
      else []
    ) defs;

  allPriorityViolations =
    checkSelectorPriorityViolations options_.corePolicyOwnership
    ++ checkSelectorPriorityViolations options_.rolesR3
    ++ checkSelectorPriorityViolations options_.stockApplications
    ++ checkPriorityViolations optChromePreferences
    ++ checkPriorityViolations optChromeExtensions
    ++ checkPriorityViolations optChromeNMH
    ++ checkPriorityViolations optFirefoxPolicies
    ++ checkPriorityViolations optFirefoxPoliciesPayload
    ++ checkPriorityViolations optFirefoxNMH
    ++ checkSelectorPriorityViolations optFirefoxPolicyDelivery
    ++ checkPriorityViolations optVivaldiPreferences
    ++ checkPriorityViolations optVivaldiExtensions
    ++ checkPriorityViolations optVivaldiNMH
    ++ checkPriorityViolations optFloorpPolicies;

  allDuplicateViolations =
    checkDuplicateKeyViolations optChromePreferences
    ++ checkDuplicateKeyViolations optChromeExtensions
    ++ checkDuplicateKeyViolations optChromeNMH
    ++ checkDuplicateKeyViolations optFirefoxPolicies
    ++ checkDuplicatePayloadViolations optFirefoxPoliciesPayload
    ++ checkDuplicateKeyViolations optFirefoxNMH
    ++ checkDuplicateKeyViolations optVivaldiPreferences
    ++ checkDuplicateKeyViolations optVivaldiExtensions
    ++ checkDuplicateKeyViolations optVivaldiNMH
    ++ checkDuplicateKeyViolations optFloorpPolicies;

  allIdentifierViolations =
    checkKeyFormatViolations optChromeExtensions "[a-p]{32}" "Chrome extension ID"
    ++ checkKeyFormatViolations optChromeNMH "[a-zA-Z0-9_.-]+" "Chrome NativeMessagingHosts name"
    ++ checkKeyFormatViolations optFirefoxNMH "[a-zA-Z0-9_.-]+" "Firefox NativeMessagingHosts name"
    ++ checkKeyFormatViolations optVivaldiExtensions "[a-p]{32}" "Vivaldi extension ID"
    ++ checkKeyFormatViolations optVivaldiNMH "[a-zA-Z0-9_.-]+" "Vivaldi NativeMessagingHosts name";

  # 4. Legacy-vs-destination exclusivity check:
  hasDefs = opt:
    opt.isDefined && builtins.any (d:
      if builtins.isAttrs d.value then
        d.value != {}
      else
        d.value != null
    ) (opt.definitionsWithLocations or []);

  hasExternalChromePrefs = hasDefs optChromePreferences;
  hasExternalChromeExts = hasDefs optChromeExtensions;
  hasExternalChromeNMH = hasDefs optChromeNMH;
  hasExternalFirefoxPolicies = hasDefs optFirefoxPolicies;
  hasExternalFirefoxPayload = hasDefs optFirefoxPoliciesPayload;
  hasExternalFirefoxNMH = hasDefs optFirefoxNMH;
  hasExternalFirefoxDelivery =
    optFirefoxPolicyDelivery.isDefined && (cfg.firefox.policyDelivery != "bundle");
  hasExternalVivaldiPrefs = hasDefs optVivaldiPreferences;
  hasExternalVivaldiExts = hasDefs optVivaldiExtensions;
  hasExternalVivaldiNMH = hasDefs optVivaldiNMH;
  hasExternalFloorpPolicies = hasDefs optFloorpPolicies;

  hasExternalPrimaryContribution =
    hasExternalChromePrefs || hasExternalChromeExts || hasExternalChromeNMH
    || hasExternalFirefoxPolicies || hasExternalFirefoxPayload || hasExternalFirefoxNMH
    || hasExternalFirefoxDelivery
    || hasExternalFloorpPolicies;
  hasExternalVivaldiContribution =
    hasExternalVivaldiPrefs || hasExternalVivaldiExts || hasExternalVivaldiNMH;
  # R3R4 retains Vivaldi's separate policy targets alongside the unchanged
  # Chrome/Firefox legacy baseline. All duplicate, priority and ID guards apply.
  retainedVivaldiReference = cfg.rolesR3 && cfg.corePolicyOwnership
    && roleOwnership.continuity.vivaldi == "VIVALDI_VISUAL_REFERENCE_RETAINED_DECOMMISSION_HELD";
  vivaldiPolicyOwnership = cfg.replacementPolicyOwnership || cfg.vivaldiPolicyOwnership;

  legacyExclusivityViolations =
    if cfg.legacyPolicyDefaults.enable && (hasExternalPrimaryContribution
      || (hasExternalVivaldiContribution && !retainedVivaldiReference)) then
      [{
        type = "legacy-exclusivity";
        option = "programs.browserBaseline.legacyPolicyDefaults.enable";
        loc = [ "programs" "browserBaseline" "legacyPolicyDefaults" "enable" ];
        message = "programs.browserBaseline.legacyPolicyDefaults.enable is true, but external browser baseline contributions are present. Legacy defaults and destination contributions must never merge.";
      }]
    else [];

  # 5. Firefox payload vs structured exclusivity check:
  firefoxPayloadConflictViolations =
    let
      hasPayload = optFirefoxPoliciesPayload.isDefined && (cfg.firefox.policiesJsonPayload != null);
      hasPolicies = optFirefoxPolicies.isDefined && (cfg.firefox.policies != {});
    in
      if hasPayload && hasPolicies then
        [{
          type = "conflicting-definitions";
          option = "programs.browserBaseline.firefox";
          loc = [ "programs" "browserBaseline" "firefox" ];
          message = "Both programs.browserBaseline.firefox.policies and programs.browserBaseline.firefox.policiesJsonPayload are defined. Destinations must contribute either structured policies or an immutable payload, not both.";
        }]
      else [];

  # 6. Firefox macOS preferences vs opaque payload exclusivity check:
  firefoxDeliveryPayloadViolations =
    let
      hasPayload = optFirefoxPoliciesPayload.isDefined && (cfg.firefox.policiesJsonPayload != null);
    in
      if cfg.firefox.policyDelivery == "macos-preferences" && hasPayload then
        [{
          type = "unsupported-delivery-payload";
          option = "programs.browserBaseline.firefox.policiesJsonPayload";
          loc = [ "programs" "browserBaseline" "firefox" "policiesJsonPayload" ];
          message = "programs.browserBaseline.firefox.policiesJsonPayload cannot be used with policyDelivery = \"macos-preferences\". macOS preferences delivery requires structured firefox.policies.";
        }]
      else [];

  # 7. Firefox reserved key check (EnterprisePoliciesEnabled is managed automatically by core):
  firefoxReservedKeyViolations =
    let
      defs = optFirefoxPolicies.definitionsWithLocations or [];
      hasReserved = builtins.any (d:
        builtins.isAttrs d.value && (d.value ? EnterprisePoliciesEnabled)
      ) defs;
    in
      if hasReserved then
        [{
          type = "reserved-key";
          option = "programs.browserBaseline.firefox.policies.EnterprisePoliciesEnabled";
          loc = [ "programs" "browserBaseline" "firefox" "policies" "EnterprisePoliciesEnabled" ];
          message = "Key EnterprisePoliciesEnabled is reserved and managed automatically by the seam. It must not be set in programs.browserBaseline.firefox.policies.";
        }]
      else [];

  # 8. Floorp reserved key check (EnterprisePoliciesEnabled is managed automatically by core):
  floorpReservedKeyViolations =
    let
      defs = optFloorpPolicies.definitionsWithLocations or [];
      hasReserved = builtins.any (d:
        builtins.isAttrs d.value && (d.value ? EnterprisePoliciesEnabled)
      ) defs;
    in
      if hasReserved then
        [{
          type = "reserved-key";
          option = "programs.browserBaseline.floorp.policies.EnterprisePoliciesEnabled";
          loc = [ "programs" "browserBaseline" "floorp" "policies" "EnterprisePoliciesEnabled" ];
          message = "Key EnterprisePoliciesEnabled is reserved and managed automatically by the seam. It must not be set in programs.browserBaseline.floorp.policies.";
        }]
      else [];

  # 9. Null value checks for macOS preference plist generators:
  hasNullValues = val:
    if val == null then
      true
    else if builtins.isAttrs val then
      builtins.any hasNullValues (builtins.attrValues val)
    else if builtins.isList val then
      builtins.any hasNullValues val
    else
      false;

  firefoxNullValueViolations =
    let
      defs = optFirefoxPolicies.definitionsWithLocations or [];
      hasNull = builtins.any (d:
        builtins.isAttrs d.value && hasNullValues d.value
      ) defs;
    in
      if cfg.firefox.policyDelivery == "macos-preferences" && hasNull then
        [{
          type = "unsupported-null-value";
          option = "programs.browserBaseline.firefox.policies";
          loc = [ "programs" "browserBaseline" "firefox" "policies" ];
          message = "programs.browserBaseline.firefox.policies contains null values, which are not supported by the macOS plist generator. Omit attributes or use explicit empty structures instead.";
        }]
      else [];

  floorpNullValueViolations =
    let
      defs = optFloorpPolicies.definitionsWithLocations or [];
      hasNull = builtins.any (d:
        builtins.isAttrs d.value && hasNullValues d.value
      ) defs;
    in
      if hasNull then
        [{
          type = "unsupported-null-value";
          option = "programs.browserBaseline.floorp.policies";
          loc = [ "programs" "browserBaseline" "floorp" "policies" ];
          message = "programs.browserBaseline.floorp.policies contains null values, which are not supported by the macOS plist generator. Omit attributes or use explicit empty structures instead.";
        }]
      else [];

  vivaldiNullValueViolations =
    let
      defs = optVivaldiPreferences.definitionsWithLocations or [];
      hasNull = builtins.any (d:
        builtins.isAttrs d.value && hasNullValues d.value
      ) defs;
    in
      if hasNull then
        [{
          type = "unsupported-null-value";
          option = "programs.browserBaseline.vivaldi.managedPreferences";
          loc = [ "programs" "browserBaseline" "vivaldi" "managedPreferences" ];
          message = "programs.browserBaseline.vivaldi.managedPreferences contains null values, which are not supported by the macOS plist generator. Omit attributes or use explicit empty structures instead.";
        }]
      else [];

  # The reusable contribution seam remains available on explicitly opted-out
  # hosts. On core-owned hosts it cannot accept Chrome or Safari definitions,
  # including noncolliding Chromium-family projections from Browser UX.
  stringLeaves = value:
    if builtins.isString value then [ value ]
    else if builtins.isAttrs value && (value._type or "") == "if" then
      if value.condition then stringLeaves value.content else []
    else if builtins.isAttrs value && builtins.elem (value._type or "") [ "order" "override" ] then
      stringLeaves value.content
    else if builtins.isAttrs value then lib.concatMap stringLeaves (builtins.attrValues value)
    else if builtins.isList value then lib.concatMap stringLeaves value
    else [];
  externalWriterViolations = lib.concatMap (definition:
    let
      frameworkDefinition = builtins.elem definition.file frameworkActivationFiles;
      aggregate = builtins.isAttrs definition.value && definition.value ? script;
      # Never force the aggregate text. Any non-framework aggregate definition
      # is rejected by its key/provenance, even if its value recursively refers
      # to core's postActivation. Only independent fragments are scanned.
      fragments = if builtins.isAttrs definition.value
        then builtins.removeAttrs definition.value [ "script" ] else definition.value;
    in lib.optional (definition.file != coreWriterFile && (
      (aggregate && !frameworkDefinition) || builtins.any (text:
      builtins.any (target: lib.hasInfix target text) [
        "com.google.Chrome" "com.apple.Safari" "/Google/Chrome/External Extensions/" "/Google/Chrome/NativeMessagingHosts/"
        "com.brave.Browser" "com.kagi.kagimacOS" "io.gitlab.librewolf-community"
        "/BraveSoftware/Brave-Browser/" "/LibreWolf.app/Contents/Resources/distribution/"
      ] && builtins.any (verb: lib.hasInfix verb text) [
        "install " "defaults write " "cp " "ln -" "rm " "plutil " "PlistBuddy " "mv " "tee " "ditto "
      ]
    ) (stringLeaves fragments))) {
      type = "core-policy-writer";
      option = "system.activationScripts";
      message = "An external activation fragment attempts to write core-owned browser targets.";
    }
  ) (options.system.activationScripts.definitionsWithLocations or []);

  # Acquisition owns the stock application identity even while policy is inactive.
  stockApplicationViolations = lib.optionals stockBraveAndLibreWolf (
    let
      # Raw submodule definitions may be functions or use config.name. Those
      # names are checked on the merged casks below instead of being forced here.
      caskName = c: if builtins.isString c then c
        else if builtins.isAttrs c then c.name or null else null;
      braveCasks = [ "brave-browser" "brave-browser@beta" "brave-browser@nightly" ];
      externalBraveCask = builtins.any (definition:
        definition.file != coreWriterFile
        && builtins.any (c: builtins.elem (caskName c) braveCasks) definition.value
      ) (options.homebrew.casks.definitionsWithLocations or []);
      stableCasks = builtins.filter (c: caskName c == "brave-browser") config.homebrew.casks;
      otherChannels = builtins.any (c: builtins.elem (caskName c)
        [ "brave-browser@beta" "brave-browser@nightly" ]) config.homebrew.casks;
      nixBrave = builtins.any (p:
        builtins.elem (lib.getName p) [ "brave" "brave-beta" "brave-nightly" ]
        && toString p != toString stockBraveCLI
      ) config.environment.systemPackages;
      cliPresent = builtins.any (p:
        lib.getName p == "brave" && toString p == toString stockBraveCLI
      ) config.environment.systemPackages;
    in
    lib.optional (externalBraveCask || otherChannels || builtins.length stableCasks != 1) {
      type = "duplicate-application";
      option = "homebrew.casks";
      message = "Core exclusively acquires stock Brave through the stable brave-browser cask; remove competing stable, beta or nightly cask declarations.";
    }
    ++ lib.optional nixBrave {
      type = "duplicate-application";
      option = "environment.systemPackages";
      message = "Stock Brave must use the writable brave-browser cask; pkgs.brave is reserved for the separate Brave YouTube source.";
    }
    ++ lib.optional (!cliPresent) {
      type = "missing-application";
      option = "environment.systemPackages";
      message = "Stock Brave requires its canonical cask CLI adapter in environment.systemPackages.";
    }
    # A priority wrapper must not suppress the owned cask declaration, including
    # replacement by an opaque function-form submodule with the same stable name.
    ++ lib.optional (options.homebrew.casks.highestPrio != 100) {
      type = "priority-override";
      option = "homebrew.casks";
      message = "Stock Brave acquisition requires the ordinary-priority owned cask declaration.";
    }
  );

  coreOwnershipViolations = lib.optionals cfg.corePolicyOwnership (
    externalWriterViolations ++
    lib.concatMap (opt:
      lib.optional (hasDefs opt) {
        type = "core-policy-ownership";
        option = lib.showOption opt.loc;
        message = "${lib.showOption opt.loc} is exclusively defined by core on this host; remove external contributions.";
      }
    ) [ optChromePreferences optChromeExtensions optChromeNMH optSafariPreferences optSafariExtensions ]
    ++ lib.optional (!cfg.chrome.enable) {
      type = "core-policy-ownership";
      option = "programs.browserBaseline.chrome.enable";
      message = "Core-owned development Chrome must remain installed and enabled.";
    }
    ++ lib.optional (!cfg.firefox.enable) {
      type = "browser-retirement-held";
      option = "programs.browserBaseline.firefox.enable";
      message = "Normal Firefox retirement is held; preserve its installation and policies.";
    }
    ++ lib.concatMap (surface:
      let prefs = config.system.defaults.${surface} or {}; in
      lib.optional (builtins.any (domain:
        builtins.elem domain [ "com.google.Chrome" "com.apple.Safari" "com.apple.Safari.SandboxBroker"
          "com.brave.Browser" "com.kagi.kagimacOS" "io.gitlab.librewolf-community" ]
      ) (builtins.attrNames prefs)) {
        type = "core-policy-ownership";
        option = "system.defaults.${surface}";
        message = "Chrome/Safari custom defaults would create a second policy writer; use core's owned contract.";
      }
    ) [ "CustomSystemPreferences" "CustomUserPreferences" ]
  );

  guardViolations =
    allPriorityViolations
    ++ allDuplicateViolations
    ++ allIdentifierViolations
    ++ legacyExclusivityViolations
    ++ firefoxPayloadConflictViolations
    ++ firefoxDeliveryPayloadViolations
    ++ firefoxReservedKeyViolations
    ++ firefoxNullValueViolations
    ++ floorpReservedKeyViolations
    ++ floorpNullValueViolations
    ++ vivaldiNullValueViolations
    ++ stockApplicationViolations
    ++ coreOwnershipViolations
    ++ extensionDeliveryViolations;

  # Effective contribution resolution
  effectiveChromePreferences =
    if guardViolations != [] || !cfg.chrome.enable then
      {}
    else if cfg.rolesR3 then
      legacyChromePolicyDefaults
    else if cfg.corePolicyOwnership then
      coreChromePolicyDefaults
    else if cfg.legacyPolicyDefaults.enable then
      legacyChromePolicyDefaults
    else if optChromePreferences.isDefined then
      cfg.chrome.managedPreferences
    else
      {};

  effectiveChromeExtensions =
    if guardViolations != [] || !cfg.chrome.enable then
      {}
    else if cfg.rolesR3 then
      { "felphkbfjadmcejnibcmcncimlappdde" = legacyClearNewTabExternalExtension; }
    else if cfg.corePolicyOwnership then
      {}
    else if cfg.legacyPolicyDefaults.enable then
      { "felphkbfjadmcejnibcmcncimlappdde" = legacyClearNewTabExternalExtension; }
    else if optChromeExtensions.isDefined then
      cfg.chrome.externalExtensions
    else
      {};

  effectiveChromeNMH =
    if guardViolations != [] || !cfg.chrome.enable then
      {}
    else if cfg.rolesR3 then
      { "org.keepassxc.keepassxc_browser" = legacyKeepassxcChromeNativeMessaging; }
    else if cfg.corePolicyOwnership then
      {}
    else if cfg.legacyPolicyDefaults.enable then
      { "org.keepassxc.keepassxc_browser" = legacyKeepassxcChromeNativeMessaging; }
    else if optChromeNMH.isDefined then
      cfg.chrome.nativeMessagingHosts
    else
      {};

  effectiveFirefoxPolicies =
    if guardViolations != [] || !cfg.firefox.enable then
      {}
    else if optFirefoxPolicies.isDefined then
      cfg.firefox.policies
    else
      {};

  effectiveFirefoxPreferences =
    if guardViolations != [] || !cfg.firefox.enable then
      {}
    else if cfg.firefox.policyDelivery != "macos-preferences" then
      {}
    else if effectiveFirefoxPolicies != {} then
      { EnterprisePoliciesEnabled = true; } // (builtins.removeAttrs effectiveFirefoxPolicies [ "EnterprisePoliciesEnabled" ])
    else
      {};

  effectiveFirefoxPoliciesFile =
    if guardViolations != [] || !cfg.firefox.enable then
      null
    else if cfg.firefox.policyDelivery == "macos-preferences" then
      null
    else if cfg.legacyPolicyDefaults.enable then
      legacyFirefoxPolicies
    else if optFirefoxPoliciesPayload.isDefined && (cfg.firefox.policiesJsonPayload != null) then
      cfg.firefox.policiesJsonPayload
    else if effectiveFirefoxPolicies != {} then
      pkgs.writeText "policies.json" (builtins.toJSON { policies = effectiveFirefoxPolicies; })
    else
      null;

  effectiveFirefoxNMH =
    if guardViolations != [] then
      {}
    else if cfg.legacyPolicyDefaults.enable then
      { "org.keepassxc.keepassxc_browser" = legacyKeepassxcFirefoxNativeMessaging; }
    else if optFirefoxNMH.isDefined then
      cfg.firefox.nativeMessagingHosts
    else
      {};

  effectiveVivaldiPreferences =
    if guardViolations != [] then
      {}
    else if optVivaldiPreferences.isDefined then
      cfg.vivaldi.managedPreferences
    else
      {};

  effectiveVivaldiExtensions =
    if guardViolations != [] then
      {}
    else if optVivaldiExtensions.isDefined then
      cfg.vivaldi.externalExtensions
    else
      {};

  effectiveVivaldiNMH =
    if guardViolations != [] then
      {}
    else if optVivaldiNMH.isDefined then
      cfg.vivaldi.nativeMessagingHosts
    else
      {};

  effectiveFloorpPolicies =
    if guardViolations != [] then
      {}
    else if optFloorpPolicies.isDefined then
      cfg.floorp.policies
    else
      {};

  effectiveFloorpPreferences =
    if guardViolations != [] then
      {}
    else if effectiveFloorpPolicies != {} then
      { EnterprisePoliciesEnabled = true; } // (builtins.removeAttrs effectiveFloorpPolicies [ "EnterprisePoliciesEnabled" ])
    else
      {};

  resolveArtifactFile = name: value:
    if builtins.isPath value then
      value
    else if builtins.isAttrs value && (value ? payload) then
      value.payload
    else if builtins.isAttrs value then
      pkgs.writeText "${name}.json" (builtins.toJSON value)
    else
      throw "Invalid artifact value for ${name}";

  effectiveChromeExtensionFiles =
    lib.mapAttrs resolveArtifactFile effectiveChromeExtensions;

  effectiveChromeNMHFiles =
    lib.mapAttrs resolveArtifactFile effectiveChromeNMH;

  effectiveFirefoxNMHFiles =
    lib.mapAttrs resolveArtifactFile effectiveFirefoxNMH;

  effectiveVivaldiExtensionFiles =
    lib.mapAttrs resolveArtifactFile effectiveVivaldiExtensions;

  effectiveVivaldiNMHFiles =
    lib.mapAttrs resolveArtifactFile effectiveVivaldiNMH;

  chromeManagedPlist =
    if effectiveChromePreferences != {} then
      pkgs.writeText "com.google.Chrome.plist" (lib.generators.toPlist { escape = true; } effectiveChromePreferences)
    else
      null;

  firefoxManagedPlist =
    if guardViolations != [] then
      null
    else if cfg.firefox.policyDelivery == "macos-preferences" && effectiveFirefoxPreferences != {} then
      pkgs.writeText "org.mozilla.firefox.plist" (lib.generators.toPlist { escape = true; } effectiveFirefoxPreferences)
    else
      null;

  vivaldiManagedPlist =
    if guardViolations != [] then
      null
    else if effectiveVivaldiPreferences != {} then
      pkgs.writeText "com.vivaldi.Vivaldi.plist" (lib.generators.toPlist { escape = true; } effectiveVivaldiPreferences)
    else
      null;

  floorpManagedPlist =
    if guardViolations != [] then
      null
    else if effectiveFloorpPreferences != {} then
      pkgs.writeText "app.floorp.Floorp.plist" (lib.generators.toPlist { escape = true; } effectiveFloorpPreferences)
    else
      null;

  # KeePassXC's supported Vivaldi installer uses the primary user's Library.
  # Do not invent a second system root from Chromium family membership.
  primaryUser = config.system.primaryUser or null;
  primaryHome = if primaryUser == null then null else config.users.users.${primaryUser}.home;
  vivaldiNativeRoot = if effectiveVivaldiNMHFiles == {} then null
    else if primaryHome == null then throw "Vivaldi native delivery requires the configured primary user's home"
    else "${primaryHome}/Library/Application Support/Vivaldi/NativeMessagingHosts";

  attendedTargets =
    lib.optional (chromeManagedPlist != null) { target = "/Library/Managed Preferences/com.google.Chrome.plist"; source = chromeManagedPlist; }
    ++ lib.optionals (cfg.corePolicyOwnership && !cfg.rolesR3) [
      { target = "/Library/Preferences/com.google.Chrome.plist";
        source = null; action = "remove-owned"; expectedSource = legacyChromePolicyFile; }
      { target = "/Library/Application Support/Google/Chrome/External Extensions/felphkbfjadmcejnibcmcncimlappdde.json";
        source = null; action = "remove-owned"; expectedSource = legacyClearNewTabExternalExtension; }
      { target = "/Library/Google/Chrome/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json";
        source = null; action = "remove-owned"; expectedSource = legacyKeepassxcChromeNativeMessaging; }
    ]
    ++ lib.optional (firefoxManagedPlist != null) { target = "/Library/Preferences/org.mozilla.firefox.plist"; source = firefoxManagedPlist; }
    ++ lib.optional (vivaldiManagedPlist != null) { target = "/Library/Managed Preferences/com.vivaldi.Vivaldi.plist"; source = vivaldiManagedPlist; }
    ++ lib.optional (floorpManagedPlist != null) { target = "/Library/Preferences/app.floorp.Floorp.plist"; source = floorpManagedPlist; }
    ++ lib.mapAttrsToList (id: source: { target = "/Library/Application Support/Google/Chrome/External Extensions/${id}.json"; inherit source; }) effectiveChromeExtensionFiles
    ++ lib.mapAttrsToList (id: source: { target = "/Library/Application Support/Vivaldi/External Extensions/${id}.json"; inherit source; }) effectiveVivaldiExtensionFiles
    ++ lib.mapAttrsToList (name: source: { target = "/Library/Google/Chrome/NativeMessagingHosts/${name}.json"; inherit source; }) effectiveChromeNMHFiles
    ++ lib.mapAttrsToList (name: source: { target = "/Library/Application Support/Mozilla/NativeMessagingHosts/${name}.json"; inherit source; }) effectiveFirefoxNMHFiles
    ++ lib.mapAttrsToList (name: source: { target = "${vivaldiNativeRoot}/${name}.json"; inherit source; perUser = true; }) effectiveVivaldiNMHFiles;
  retirementTargets =
    lib.optionals (!cfg.chrome.enable) (
      map (target: { inherit target; source = null; action = "remove"; }) [
        "/Library/Managed Preferences/com.google.Chrome.plist"
        "/Library/Application Support/Google/Chrome/External Extensions/felphkbfjadmcejnibcmcncimlappdde.json"
        "/Library/Google/Chrome/NativeMessagingHosts/org.keepassxc.keepassxc_browser.json"
      ])
    ++ lib.optionals (!cfg.firefox.enable) (
      map (target: { inherit target; source = null; action = "remove"; }) [
        "/Library/Preferences/org.mozilla.firefox.plist"
        "/Applications/Firefox.app/Contents/Resources/distribution/policies.json"
      ]);
  selectedAttendedTargets = attendedTargets ++ retirementTargets;
  makeAttendedApply = name: targets: pkgs.writeShellScript name ''
    ${coreDeploymentGuard}
    set -eu
    test "$(id -u)" -eq 0
    mode=apply
    backup=""
    if [ "$#" -gt 0 ]; then
      case "$1" in
        --backup) backup="$2" ;;
        --rollback) mode=rollback; backup="$2" ;;
        *) exit 64 ;;
      esac
      test "$#" -eq 2
      test -d "$backup"
      test ! -L "$backup"
      test "$(cd "$backup" && pwd -P)" = "$backup"
      if [ "$mode" = rollback ]; then
        test "$(cat "$backup/writer")" = "$0"
      else
        test -z "$(ls -A "$backup")"
        printf '%s\n' "$0" > "$backup/writer"
      fi
    fi
    ${lib.optionalString (cfg.corePolicyOwnership && builtins.any (row:
      lib.hasInfix "com.google.Chrome" row.target
    ) targets) coreChromeRecommendedPreflight}
    # Back up the whole lane before its first write. A partial apply has a complete rollback set.
    if [ "$mode" = apply ] && [ -n "$backup" ]; then
      :
      ${lib.concatImapStrings (index: row: ''
        target=${lib.escapeShellArg row.target}
        ancestor=${lib.escapeShellArg (builtins.dirOf row.target)}
        while [ "$ancestor" != / ]; do
          test ! -L "$ancestor"
          ancestor="$(dirname "$ancestor")"
        done
        if [ -L "$target" ]; then
          printf 'link\n' > "$backup/${toString index}.kind"
          readlink "$target" > "$backup/${toString index}.link"
        elif [ -f "$target" ]; then
          printf 'file\n' > "$backup/${toString index}.kind"
          cp -p "$target" "$backup/${toString index}.before"
        elif [ ! -e "$target" ]; then
          printf 'absent\n' > "$backup/${toString index}.kind"
        else
          exit 65
        fi
      '') targets}
    fi
    if [ "$mode" = rollback ]; then
      :
      ${lib.concatImapStrings (index: _: ''
        test -f "$backup/${toString index}.kind"
        case "$(cat "$backup/${toString index}.kind")" in
          absent) : ;;
          link) test -f "$backup/${toString index}.link" ;;
          file) test -f "$backup/${toString index}.before"; test ! -L "$backup/${toString index}.before" ;;
          *) exit 65 ;;
        esac
      '') targets}
    fi
    ${lib.concatImapStrings (index: row: ''
      target=${lib.escapeShellArg row.target}
      parent=${lib.escapeShellArg (builtins.dirOf row.target)}
      ancestor="$parent"
      while [ "$ancestor" != / ]; do
        test ! -L "$ancestor"
        ancestor="$(dirname "$ancestor")"
      done
      if [ "$mode" = rollback ]; then
        test -f "$backup/${toString index}.kind"
        case "$(cat "$backup/${toString index}.kind")" in
          absent) rm -f "$target" ;;
          link) rm -f "$target"; ln -s "$(cat "$backup/${toString index}.link")" "$target" ;;
          file)
            test -f "$backup/${toString index}.before"
            test ! -L "$backup/${toString index}.before"
            rm -f "$target"
            cp -p "$backup/${toString index}.before" "$target"
            ;;
          *) exit 65 ;;
        esac
      else
      ${if (row.action or "install") == "remove-owned" then ''
        # Retract only byte-matching former core manifests, never a profile extension.
        if [ -e "$target" ] && cmp -s ${lib.escapeShellArg (toString row.expectedSource)} "$target"; then
          rm -f "$target"
        fi
      '' else if (row.action or "install") == "remove" then ''
        rm -f "$target"
      '' else ''
      ${lib.optionalString (lib.hasInfix "/Application Support/Vivaldi/External Extensions/" row.target) ''
        for ownedDirectory in "/Library/Application Support/Vivaldi" "/Library/Application Support/Vivaldi/External Extensions"; do
          test ! -L "$ownedDirectory"
          if [ ! -e "$ownedDirectory" ]; then
            install -d -m 0755 "$ownedDirectory"
          else
            test -d "$ownedDirectory"
            test "$(stat -f '%u' "$ownedDirectory")" -eq 0
            # Repair only the proven root-owned 0700 creation defect on these entries.
            if [ "$(stat -f '%Lp' "$ownedDirectory")" = 700 ]; then chmod 0755 "$ownedDirectory"; fi
          fi
        done
      ''}
      if [ ! -d "$parent" ]; then
        install -d -m 0755 "$parent"
        ${lib.optionalString (row.perUser or false) "chown ${lib.escapeShellArg primaryUser} \"$parent\""}
      fi
      # Break an existing manifest symlink before replacing its link entry.
      if [ -L "$target" ]; then rm "$target"; fi
      install -m 0644 ${lib.escapeShellArg (toString row.source)} "$target"
      ${lib.optionalString (row.perUser or false) "chown ${lib.escapeShellArg primaryUser} \"$target\""}
      # Privileged bytes prove the write only. The operator performs ordinary readback.
      cmp -s ${lib.escapeShellArg (toString row.source)} "$target"
      ''}
      fi
    '') targets}
    ${lib.optionalString (cfg.corePolicyOwnership && builtins.any (row:
      lib.hasInfix "com.google.Chrome" row.target
    ) targets) ''killall cfprefsd >/dev/null 2>&1 || true''}
    printf '%s\n' 'CORE_WRITER_COMPLETED_ORDINARY_READBACK_PENDING'
  '';
  attendedApply = makeAttendedApply "browser-baseline-attended-apply" selectedAttendedTargets;
  attendedTargetsByBrowser = {
    chrome = builtins.filter (row: lib.hasInfix "com.google.Chrome.plist" row.target || lib.hasInfix "/Google/Chrome/" row.target) selectedAttendedTargets;
    firefox = builtins.filter (row: lib.hasInfix "org.mozilla.firefox.plist" row.target || lib.hasInfix "/Firefox.app/" row.target || (cfg.firefox.enable && lib.hasInfix "/Mozilla/NativeMessagingHosts/" row.target)) selectedAttendedTargets;
    vivaldi = builtins.filter (row: lib.hasInfix "Vivaldi" row.target) attendedTargets;
    floorp = builtins.filter (row: lib.hasInfix "app.floorp.Floorp.plist" row.target || lib.hasInfix "/Mozilla/NativeMessagingHosts/" row.target) attendedTargets;
  };
  attendedApplyByBrowser = lib.mapAttrs (browser: targets:
    makeAttendedApply "browser-baseline-attended-${browser}" targets) attendedTargetsByBrowser;
in
{
  _file = coreWriterFile;
  options.programs.browserBaseline = {
    stockApplications = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Acquire stock Homebrew Brave, LibreWolf and Orion without
        selecting R3 policy, extensions, credentials or profile transitions.
        Core's host constructor selects this additive application set.
      '';
    };
    rolesR3 = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Inactive-by-default R3 package and role candidate; active selection remains held in source. The host constructor owns selection.";
    };
    corePolicyOwnership = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Core defines Chrome policy and Safari/Wipr desired state exclusively.
        False preserves the existing contribution seam on both hosts while R3
        is inactive. The host constructor owns this reviewed activation boundary.
        Browser UX must never set this selector.
      '';
    };
    safari = {
      managedPreferences = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        description = "Reserved Safari contribution seam; rejected on core-owned hosts. No undocumented preferences are emitted.";
      };
      requiredExtensions = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        description = "Reserved Safari extension contribution seam; core owns the attended Wipr contract.";
      };
    };
    replacementPolicyOwnership = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Explicit ownership of Vivaldi/Floorp policy targets, including retraction
        when those endpoints become absent. Leave false on unselected hosts.
        Native registrations owned by KeePassXC are outside this retraction.
      '';
    };
    vivaldiPolicyOwnership = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Ownership of Vivaldi policy and known external-extension retraction only.
        Retained references can carry legacy Vivaldi ownership without selecting
        Floorp policy retraction. The existing core physical writer is unchanged.
      '';
    };
    legacyPolicyDefaults.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Whether to render current legacy browser policies and extension configurations
        from config/browser/*. Declared with option-declaration default of true.
        Must be set to false when external or domain contributions are provided.
      '';
    };

    chrome = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = ''
          Whether Google Chrome management and package installation are enabled.
          Defaults to true to preserve legacy hosts. When set to false, disables
          system package installation and retracts owned policy, external extensions,
          and native messaging hosts.
        '';
      };

      managedPreferences = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        description = ''
          Chrome managed policy values written to /Library/Managed Preferences/com.google.Chrome.plist.
          Each attribute name is a top-level policy key (e.g. BlockThirdPartyCookies, ExtensionSettings).
        '';
      };

      externalExtensions = lib.mkOption {
        type = lib.types.attrsOf (lib.types.either lib.types.path (lib.types.attrsOf lib.types.anything));
        description = ''
          Chrome external extensions mapping extension IDs to either a manifest path, a payload record,
          or a structured attribute set. Extension IDs must be 32-character identifiers matching [a-p]{32}.
        '';
      };

      nativeMessagingHosts = lib.mkOption {
        type = lib.types.attrsOf (lib.types.either lib.types.path (lib.types.attrsOf lib.types.anything));
        description = ''
          Chrome NativeMessagingHosts mapping host names to JSON manifest paths or attribute sets.
          Host names must match [a-zA-Z0-9_.-]+.
        '';
      };
    };

    firefox = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = ''
          Whether Firefox management and cask installation are enabled.
          Defaults to true to preserve legacy hosts. When set to false, disables
          homebrew cask installation and retracts owned policy and native messaging hosts.
        '';
      };

      policyDelivery = lib.mkOption {
        type = lib.types.enum [ "bundle" "macos-preferences" ];
        default = "bundle";
        description = ''
          Delivery mechanism for Firefox enterprise policies.
          - "bundle": writes /Applications/Firefox.app/Contents/Resources/distribution/policies.json
          - "macos-preferences": writes /Library/Preferences/org.mozilla.firefox.plist
        '';
      };

      policies = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        description = ''
          Firefox enterprise policy values. When rendered without policiesJsonPayload,
          the physical writer places these under the top-level 'policies' JSON object.
        '';
      };

      policiesJsonPayload = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        description = ''
          Immutable policies.json payload file (containing the full JSON with top-level 'policies' key)
          used to preserve exact byte parity. Mutually exclusive with firefox.policies.
        '';
      };

      nativeMessagingHosts = lib.mkOption {
        type = lib.types.attrsOf (lib.types.either lib.types.path (lib.types.attrsOf lib.types.anything));
        description = ''
          Firefox NativeMessagingHosts mapping host names to JSON manifest paths or attribute sets.
          Host names must match [a-zA-Z0-9_.-]+.
        '';
      };
    };

    vivaldi = {
      managedPreferences = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        description = ''
          Vivaldi managed policy values written to /Library/Managed Preferences/com.vivaldi.Vivaldi.plist.
          Each attribute name is a top-level policy key.
        '';
      };

      externalExtensions = lib.mkOption {
        type = lib.types.attrsOf (lib.types.either lib.types.path (lib.types.attrsOf lib.types.anything));
        description = ''
          Vivaldi external extensions mapping extension IDs to either a manifest path, a payload record,
          or a structured attribute set. Extension IDs must be 32-character identifiers matching [a-p]{32}.
        '';
      };

      nativeMessagingHosts = lib.mkOption {
        type = lib.types.attrsOf (lib.types.either lib.types.path (lib.types.attrsOf lib.types.anything));
        description = ''
          Vivaldi NativeMessagingHosts mapping host names to JSON manifest paths or attribute sets.
          Host names must match [a-zA-Z0-9_.-]+.
        '';
      };
    };

    extensionContributions = lib.mkOption {
      type = lib.types.listOf (lib.types.attrsOf lib.types.anything);
      default = [];
      description = ''
        Per-role, per-ID semantic extension data for R3 attended delivery.
        Core owns security/credentials on core-installed browsers; Browser UX
        owns convenience there and the Floorp suite. Core alone renders the
        combined plan. This option writes no policies or profile data.
      '';
    };

    floorp = {
      policies = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        description = ''
          Floorp enterprise policy values written to /Library/Preferences/app.floorp.Floorp.plist.
        '';
      };
    };
  };

  config = {
    environment.systemPackages = lib.optional cfg.chrome.enable pkgs.google-chrome
      ++ lib.optional stockBraveAndLibreWolf stockBraveCLI
      ++ lib.optional cfg.rolesR3 automationBrowser;

    homebrew.casks = lib.optional cfg.firefox.enable "firefox"
      ++ lib.optional stockBraveAndLibreWolf "brave-browser"
      ++ lib.optional stockBraveAndLibreWolf "librewolf"
      ++ lib.optional stockOrion "orion";

    assertions = [
      {
        assertion = guardViolations == [];
        message = "Browser baseline seam guard violations:\n"
          + lib.concatMapStringsSep "\n" (v: "  - [${v.type}] ${v.message}") guardViolations;
      }
      {
        assertion = !coreDeploymentHeld;
        message = coreDeploymentHoldMessage;
      }
      {
        assertion = !cfg.rolesR3 || cfg.corePolicyOwnership;
        message = "R3 roles require core ownership and its mechanical deployment hold.";
      }
      {
        assertion = !cfg.corePolicyOwnership || cfg.rolesR3;
        message = "Core ownership requires R3 roles; the retired R2B projection cannot be adopted.";
      }
    ];

    system.build.browserBaseline = {
      policyDefinitionOwner = if cfg.corePolicyOwnership then "core" else "contribution-seam";
      ownershipContract = if cfg.rolesR3 then roleOwnership else coreOwnership;
      rolesR3 = if cfg.rolesR3 then roleOwnership else null;
      # Historical compatibility surface; rejected ownership claims are not current.
      protonVpnR3R2 = if cfg.rolesR3 then {
        current = false;
        status = "SUPERSEDED_HISTORICAL_R3R2";
        supersededBy = "protonVpnR3R3";
      } else null;
      protonVpnR3R3 = if cfg.rolesR3 then protonDelivery else null;
      extensionDeliveryR3R3 = if cfg.rolesR3 && guardViolations == [] then {
        schema = "browser-extension-delivery-r3r3-v1";
        semanticEntries = extensionDelivery.entries;
        physicalPolicyWriter = null; # Attended per-profile installs, no new policy.
        canonicalPlanWriter = "core:modules/browsers.nix";
        runtimeInstalled = false;
        attendedPlan = pkgs.writeText "browser-extension-installation-r3r3.json"
          (builtins.toJSON (lib.mapAttrs (_: row: row // lib.optionalAttrs (row ? procedure) {
            procedure = builtins.readFile row.procedure;
          }) extensionDelivery.entries));
        presenceReadback = ../libexec/browser-extension-delivery/readback.py;
      } else null;
      automationBrowser = if cfg.rolesR3 then automationBrowser else null;
      deploymentHeld = coreDeploymentHeld;
      deploymentHoldMessage = coreDeploymentHoldMessage;
      chromePreferences = effectiveChromePreferences;
      safariIntent = if cfg.corePolicyOwnership || cfg.stockApplications then coreOwnership.safari else null;
      safariManagedPlist = null; # UI-only Apple settings are intent, not enforcement.
      attendedTargets = selectedAttendedTargets;
      inherit attendedApply attendedTargetsByBrowser attendedApplyByBrowser;
      attendedOwner = "core/programs.browserBaseline";
      inherit guardViolations;
      chromeEnable = cfg.chrome.enable;
      inherit chromeManagedPlist;
      chromeExternalExtensions = effectiveChromeExtensionFiles;
      chromeNativeMessagingHosts = effectiveChromeNMHFiles;
      firefoxEnable = cfg.firefox.enable;
      firefoxPolicyDelivery = cfg.firefox.policyDelivery;
      firefoxPolicies = effectiveFirefoxPoliciesFile;
      firefoxManagedPlist = firefoxManagedPlist;
      firefoxPreferences = effectiveFirefoxPreferences;
      firefoxNativeMessagingHosts = effectiveFirefoxNMHFiles;
      vivaldiManagedPlist = vivaldiManagedPlist;
      vivaldiExternalExtensions = effectiveVivaldiExtensionFiles;
      vivaldiNativeMessagingHosts = effectiveVivaldiNMHFiles;
      floorpManagedPlist = floorpManagedPlist;
      floorpPreferences = effectiveFloorpPreferences;
      effectiveState = {
        legacyDefaultsEnabled = cfg.legacyPolicyDefaults.enable;
        hasChromePreferences = effectiveChromePreferences != {};
        chromePreferenceCount = builtins.length (builtins.attrNames effectiveChromePreferences);
        externalExtensionIds = builtins.attrNames effectiveChromeExtensions;
        chromeNativeMessagingHostNames = builtins.attrNames effectiveChromeNMH;
        firefoxPolicyDelivery = cfg.firefox.policyDelivery;
        hasFirefoxPolicies = (effectiveFirefoxPoliciesFile != null) || (firefoxManagedPlist != null);
        hasFirefoxPlist = firefoxManagedPlist != null;
        firefoxNativeMessagingHostNames = builtins.attrNames effectiveFirefoxNMH;
        hasVivaldiPreferences = effectiveVivaldiPreferences != {};
        vivaldiPreferenceCount = builtins.length (builtins.attrNames effectiveVivaldiPreferences);
        vivaldiExternalExtensionIds = builtins.attrNames effectiveVivaldiExtensions;
        vivaldiNativeMessagingHostNames = builtins.attrNames effectiveVivaldiNMH;
        hasFloorpPolicies = effectiveFloorpPreferences != {};
        hasFloorpPlist = floorpManagedPlist != null;
        floorpPolicyCount = builtins.length (builtins.attrNames effectiveFloorpPolicies);
      };
    };

    system.activationScripts.postActivation.text = ''
      ${coreDeploymentGuard}
      ${coreChromeRecommendedPreflight}
      echo "setting up browser policies..."

      # Chrome Managed Preferences
      ${if chromeManagedPlist != null then ''
        install -d -m 0755 "/Library/Managed Preferences"
        ${if cfg.corePolicyOwnership then ''
        install -m 0644 "${chromeManagedPlist}" "/Library/Managed Preferences/com.google.Chrome.plist.tmp.$$"
        mv -f "/Library/Managed Preferences/com.google.Chrome.plist.tmp.$$" "/Library/Managed Preferences/com.google.Chrome.plist"
        '' else ''
        install -m 0644 "${chromeManagedPlist}" "/Library/Managed Preferences/com.google.Chrome.plist"
        ''}
      '' else ''
        if [ -f "/Library/Managed Preferences/com.google.Chrome.plist" ]; then
          rm -f "/Library/Managed Preferences/com.google.Chrome.plist"
        fi
      ''}

      # Stale Chrome preference cleanup
      ${lib.optionalString (!cfg.corePolicyOwnership) ''
      ${lib.optionalString (!(effectiveChromePreferences ? PasswordManagerEnabled)) ''defaults delete "/Library/Managed Preferences/com.google.Chrome" PasswordManagerEnabled >/dev/null 2>&1 || true''}
      defaults delete "/Library/Preferences/com.google.Chrome" PasswordManagerEnabled >/dev/null 2>&1 || true
      ${lib.optionalString (!(effectiveChromePreferences ? AutofillCreditCardEnabled)) ''defaults delete "/Library/Managed Preferences/com.google.Chrome" AutofillCreditCardEnabled >/dev/null 2>&1 || true''}
      defaults delete "/Library/Preferences/com.google.Chrome" AutofillCreditCardEnabled >/dev/null 2>&1 || true
      ${lib.optionalString (!(effectiveChromePreferences ? AutofillAddressEnabled)) ''defaults delete "/Library/Managed Preferences/com.google.Chrome" AutofillAddressEnabled >/dev/null 2>&1 || true''}
      defaults delete "/Library/Preferences/com.google.Chrome" AutofillAddressEnabled >/dev/null 2>&1 || true
      ''}

      # Preserve the baseline cache/recommended-policy ordering while inactive.
      ${lib.optionalString (!cfg.rolesR3) ''killall cfprefsd >/dev/null 2>&1 || true''}
      # R3 keeps both existing Chrome provenance paths as continuity. The older
      # projection's byte-matched recommended retraction remains compatibility-only.
      ${lib.optionalString (!cfg.rolesR3) ''
      recommendedChromePolicy="/Library/Preferences/com.google.Chrome.plist"
      if [ -f "$recommendedChromePolicy" ] && cmp -s "${legacyChromePolicyFile}" "$recommendedChromePolicy"; then
        rm "$recommendedChromePolicy"
      fi
      ''}
      # Invalidate the managed-preference cache in both ownership lanes.
      ${lib.optionalString cfg.rolesR3 ''killall cfprefsd >/dev/null 2>&1 || true''}

      # Chrome External Extensions
      chromeExternalRoot="/Library/Application Support/Google"
      chromeExternalProfileRoot="$chromeExternalRoot/Chrome"
      chromeExternalExtensionsDir="/Library/Application Support/Google/Chrome/External Extensions"
      ${if effectiveChromeExtensionFiles != {} then ''
        install -d -m 0755 "$chromeExternalExtensionsDir"
        chown root:wheel "$chromeExternalRoot" "$chromeExternalProfileRoot" "$chromeExternalExtensionsDir"
        chmod 0755 "$chromeExternalRoot" "$chromeExternalProfileRoot" "$chromeExternalExtensionsDir"
        ${lib.concatStrings (lib.mapAttrsToList (id: file: ''
          install -m 0644 "${file}" "$chromeExternalExtensionsDir/${id}.json"
          chown root:wheel "$chromeExternalExtensionsDir/${id}.json"
          chmod 0644 "$chromeExternalExtensionsDir/${id}.json"
        '') effectiveChromeExtensionFiles)}
      '' else ""}
      # Bounded cleanup for seam-owned external extensions
      ${lib.concatMapStrings (id: ''
        ${if ! (effectiveChromeExtensions ? "${id}") then ''
          if [ -f "$chromeExternalExtensionsDir/${id}.json" ]; then
            ${if cfg.corePolicyOwnership then ''
            if cmp -s "${legacyClearNewTabExternalExtension}" "$chromeExternalExtensionsDir/${id}.json"; then
              rm -f "$chromeExternalExtensionsDir/${id}.json"
            fi
            '' else ''rm -f "$chromeExternalExtensionsDir/${id}.json"''}
          fi
        '' else ""}
      '') knownExternalExtensionIdentities}

      # Chrome Native Messaging Hosts
      ${if effectiveChromeNMHFiles != {} then ''
        install -d -m 0755 "/Library/Google/Chrome/NativeMessagingHosts"
        ${lib.concatStrings (lib.mapAttrsToList (name: file: ''
          ln -sfn "${file}" "/Library/Google/Chrome/NativeMessagingHosts/${name}.json"
        '') effectiveChromeNMHFiles)}
      '' else ""}
      # Bounded cleanup for seam-owned Chrome NativeMessagingHosts symlinks
      ${lib.concatMapStrings (name: ''
        ${if ! (effectiveChromeNMH ? "${name}") then ''
          chromeNMH="/Library/Google/Chrome/NativeMessagingHosts/${name}.json"
          if [ -L "$chromeNMH" ] || [ -f "$chromeNMH" ]; then
            target="$(readlink "$chromeNMH" || true)"
            ${if cfg.corePolicyOwnership then ''
            if cmp -s "${legacyKeepassxcChromeNativeMessaging}" "$chromeNMH"; then
              rm -f "$chromeNMH"
            fi
            '' else ''case "$target" in
              /nix/store/*|*/config/browser/keepassxc-*)
                rm -f "$chromeNMH"
                ;;
            esac''}
          fi
        '' else ""}
      '') knownNativeMessagingHosts}

      # Mozilla Native Messaging Hosts
      ${if effectiveFirefoxNMHFiles != {} then ''
        install -d -m 0755 "/Library/Application Support/Mozilla/NativeMessagingHosts"
        ${lib.concatStrings (lib.mapAttrsToList (name: file: ''
          ln -sfn "${file}" "/Library/Application Support/Mozilla/NativeMessagingHosts/${name}.json"
        '') effectiveFirefoxNMHFiles)}
      '' else ""}
      # Bounded cleanup for seam-owned Mozilla NativeMessagingHosts symlinks
      ${lib.concatMapStrings (name: ''
        ${if ! (effectiveFirefoxNMH ? "${name}") && effectiveFloorpPreferences == {} then ''
          mozillaNMH="/Library/Application Support/Mozilla/NativeMessagingHosts/${name}.json"
          if [ -L "$mozillaNMH" ] || [ -f "$mozillaNMH" ]; then
            target="$(readlink "$mozillaNMH" || true)"
            case "$target" in
              /nix/store/*|*/config/browser/keepassxc-*)
                rm -f "$mozillaNMH"
                ;;
            esac
          fi
        '' else ""}
      '') knownNativeMessagingHosts}

      # Firefox Policies
      firefoxPolicyDir="/Applications/Firefox.app/Contents/Resources/distribution"
      ${if cfg.firefox.policyDelivery == "macos-preferences" then ''
        ${if firefoxManagedPlist != null then ''
          install -d -m 0755 "/Library/Preferences"
          install -o root -g wheel -m 0644 "${firefoxManagedPlist}" "/Library/Preferences/org.mozilla.firefox.plist.tmp.$$"
          mv -f "/Library/Preferences/org.mozilla.firefox.plist.tmp.$$" "/Library/Preferences/org.mozilla.firefox.plist"
          killall cfprefsd >/dev/null 2>&1 || true
        '' else ''
          if [ -f "/Library/Preferences/org.mozilla.firefox.plist" ]; then
            rm -f "/Library/Preferences/org.mozilla.firefox.plist"
            killall cfprefsd >/dev/null 2>&1 || true
          fi
        ''}
        if [ -f "$firefoxPolicyDir/policies.json" ]; then
          rm -f "$firefoxPolicyDir/policies.json"
        fi
      '' else ''
        ${if effectiveFirefoxPoliciesFile != null then ''
          if [ -d "/Applications/Firefox.app" ]; then
            install -d -m 0755 "$firefoxPolicyDir"
            install -m 0644 "${effectiveFirefoxPoliciesFile}" "$firefoxPolicyDir/policies.json"
          else
            echo "WARNING: [browserBaseline] Firefox policy is configured but /Applications/Firefox.app was not found; policies.json not installed" >&2
          fi
        '' else ''
          if [ -f "$firefoxPolicyDir/policies.json" ]; then
            rm -f "$firefoxPolicyDir/policies.json"
          fi
        ''}
        if [ -f "/Library/Preferences/org.mozilla.firefox.plist" ]; then
          rm -f "/Library/Preferences/org.mozilla.firefox.plist"
          killall cfprefsd >/dev/null 2>&1 || true
        fi
      ''}

      ${lib.optionalString (vivaldiPolicyOwnership || vivaldiManagedPlist != null || effectiveVivaldiExtensionFiles != {} || effectiveVivaldiNMHFiles != {}) ''
      # Vivaldi Managed Preferences (only an explicitly selected endpoint)
      ${if vivaldiManagedPlist != null then ''
        install -d -m 0755 "/Library/Managed Preferences"
        install -m 0644 "${vivaldiManagedPlist}" "/Library/Managed Preferences/com.vivaldi.Vivaldi.plist"
      '' else lib.optionalString vivaldiPolicyOwnership ''
        rm -f "/Library/Managed Preferences/com.vivaldi.Vivaldi.plist"
      ''}

      # Vivaldi External Extensions
      vivaldiExternalRoot="/Library/Application Support/Vivaldi"
      vivaldiExternalExtensionsDir="/Library/Application Support/Vivaldi/External Extensions"
      ${if effectiveVivaldiExtensionFiles != {} then ''
        install -d -m 0755 "$vivaldiExternalExtensionsDir"
        chown root:wheel "$vivaldiExternalRoot" "$vivaldiExternalExtensionsDir"
        chmod 0755 "$vivaldiExternalRoot" "$vivaldiExternalExtensionsDir"
        ${lib.concatStrings (lib.mapAttrsToList (id: file: ''
          install -m 0644 "${file}" "$vivaldiExternalExtensionsDir/${id}.json"
          chown root:wheel "$vivaldiExternalExtensionsDir/${id}.json"
          chmod 0644 "$vivaldiExternalExtensionsDir/${id}.json"
        '') effectiveVivaldiExtensionFiles)}
      '' else ""}
      # Only explicitly owned, known external manifests are retracted.
      ${lib.optionalString vivaldiPolicyOwnership (lib.concatMapStrings (id:
        lib.optionalString (!(effectiveVivaldiExtensions ? "${id}")) ''
          rm -f "$vivaldiExternalExtensionsDir/${id}.json"
        '') knownExternalExtensionIdentities)}

      # Vivaldi Native Messaging Hosts
      ${if effectiveVivaldiNMHFiles != {} then ''
        if [ ! -d ${lib.escapeShellArg vivaldiNativeRoot} ]; then
          install -d -m 0755 ${lib.escapeShellArg vivaldiNativeRoot}
          chown ${lib.escapeShellArg primaryUser} ${lib.escapeShellArg vivaldiNativeRoot}
        fi
        ${lib.concatStrings (lib.mapAttrsToList (name: file: ''
          ln -sfn "${file}" ${lib.escapeShellArg "${vivaldiNativeRoot}/${name}.json"}
          chown -h ${lib.escapeShellArg primaryUser} ${lib.escapeShellArg "${vivaldiNativeRoot}/${name}.json"}
        '') effectiveVivaldiNMHFiles)}
      '' else ""}
      ''}

      ${if floorpManagedPlist != null then ''
        # Floorp Policies (macOS preferences)
        install -d -m 0755 "/Library/Preferences"
        install -o root -g wheel -m 0644 "${floorpManagedPlist}" "/Library/Preferences/app.floorp.Floorp.plist.tmp.$$"
        mv -f "/Library/Preferences/app.floorp.Floorp.plist.tmp.$$" "/Library/Preferences/app.floorp.Floorp.plist"
        killall cfprefsd >/dev/null 2>&1 || true
      '' else lib.optionalString cfg.replacementPolicyOwnership ''
        rm -f "/Library/Preferences/app.floorp.Floorp.plist"
        killall cfprefsd >/dev/null 2>&1 || true
      ''}
    '';
  };
}
