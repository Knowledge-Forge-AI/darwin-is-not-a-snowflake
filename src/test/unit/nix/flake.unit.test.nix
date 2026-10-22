# nix-darwin-test-source-target: flake.nix
# nix-darwin-test-source-target: config/nix-darwin/default/flake.nix
# nix-darwin-test-source-target: modules/android.nix
# nix-darwin-test-source-target: modules/brave-youtube.nix
# nix-darwin-test-source-target: modules/brewfile-stragglers.nix
# nix-darwin-test-source-target: modules/browsers.nix
# nix-darwin-test-source-target: modules/codex-state.nix
# nix-darwin-test-source-target: modules/codex.nix
# nix-darwin-test-source-target: modules/deployment-effects.nix
# nix-darwin-test-source-target: modules/desktop-apps.nix
# nix-darwin-test-source-target: modules/developer-tooling.nix
# nix-darwin-test-source-target: modules/fastfetch.nix
# nix-darwin-test-source-target: modules/host-security.nix
# nix-darwin-test-source-target: modules/kitty.nix
# nix-darwin-test-source-target: modules/nixpkgs-policy.nix
# nix-darwin-test-source-target: modules/nvm.nix
# nix-darwin-test-source-target: modules/oh-my-zsh.nix
# nix-darwin-test-source-target: modules/password-sync.nix
# nix-darwin-test-source-target: modules/pyenv.nix
# nix-darwin-test-source-target: modules/rbenv.nix
# nix-darwin-test-source-target: modules/sdkman.nix
# nix-darwin-test-source-target: modules/shell.nix
# nix-darwin-test-source-target: modules/ssh.nix
# nix-darwin-test-source-target: modules/terminal.nix
# nix-darwin-test-source-target: modules/video-session.nix
# nix-darwin-test-source-target: modules/virtual-machines.nix
# nix-darwin-test-source-target: packages/arq_restore/default.nix
# nix-darwin-test-source-target: packages/rtk/default.nix
# nix-darwin-test-source-target: packages/repomap-kg/default.nix
# nix-darwin-test-source-target: packages/dinas-frontend-tools/default.nix
# nix-darwin-test-source-target: packages/node-bootstrap-fixture/default.nix
# nix-darwin-test-source-target: packages/malskanner/default.nix
# nix-darwin-test-source-target: packages/pypistats/default.nix
# nix-darwin-test-source-target: packages/termcolor/default.nix
{ repoRoot, system }:

let
  flake = builtins.getFlake "path:${toString repoRoot}";
  pkgs = flake.legacyPackages.${system};
  lib = pkgs.lib;
  flakeSource = builtins.readFile (repoRoot + "/flake.nix");
  flakeLock = builtins.fromJSON (builtins.readFile (repoRoot + "/flake.lock"));
  arqRestoreSource = builtins.readFile (repoRoot + "/packages/arq_restore/default.nix");
  developerToolingModule = repoRoot + "/modules/developer-tooling.nix";
  developerToolingSource =
    if builtins.pathExists developerToolingModule
    then builtins.readFile developerToolingModule
    else "";
  rtkSource = builtins.readFile (repoRoot + "/packages/rtk/default.nix");
  repomapKgSource = builtins.readFile (repoRoot + "/packages/repomap-kg/default.nix");
  frontendToolsSource = builtins.readFile (repoRoot + "/packages/dinas-frontend-tools/default.nix");
  nodeFixtureSource = builtins.readFile (repoRoot + "/packages/node-bootstrap-fixture/default.nix");
  pypistatsSource = builtins.readFile (repoRoot + "/packages/pypistats/default.nix");
  repomapKgDependenciesSource =
    let
      definitionParts = lib.splitString "  dependencies = [" repomapKgSource;
    in
    if builtins.length definitionParts > 1
    then builtins.elemAt (lib.splitString "  ];" (builtins.elemAt definitionParts 1)) 0
    else "";
  repomapKgDependencyLines = lib.splitString "\n" repomapKgDependenciesSource;
  arqRestoreTreeV4Patch = repoRoot + "/packages/arq_restore/arq7-tree-v4-node-fields.patch";
  arqRestoreTreeV4PatchSource =
    if builtins.pathExists arqRestoreTreeV4Patch
    then builtins.readFile arqRestoreTreeV4Patch
    else "";
  managedPythonSource =
    let
      afterDefinition = builtins.elemAt
        (lib.splitString "mkManagedPython = pkgs: pkgs.python3.withPackages (pythonPackages: [" flakeSource)
        1;
    in
    builtins.elemAt (lib.splitString "    ]);" afterDefinition) 0;
  testPythonSource =
    let
      definitionParts = lib.splitString "mkTestPython = pkgs: pkgs.python3.withPackages (pythonPackages: [" flakeSource;
    in
    if builtins.length definitionParts > 1
    then builtins.elemAt (lib.splitString "    ]);" (builtins.elemAt definitionParts 1)) 0
    else "";
  managedGoToolsSource =
    let
      definitionParts = lib.splitString "mkManagedGoTools = pkgs: [" flakeSource;
    in
    if builtins.length definitionParts > 1
    then builtins.elemAt (lib.splitString "    ];" (builtins.elemAt definitionParts 1)) 0
    else "";
  managedGoToolsSourceLines = lib.splitString "\n" managedGoToolsSource;
  managedRubySource =
    let
      definitionParts = lib.splitString
        "mkManagedRuby = pkgs: pkgs.ruby.withPackages (rubyPackages: ["
        flakeSource;
    in
    if builtins.length definitionParts > 1
    then builtins.elemAt (lib.splitString "    ]);" (builtins.elemAt definitionParts 1)) 0
    else "";
  managedRubySourceLines = lib.splitString "\n" managedRubySource;
  managedNodeSource =
    let
      definitionParts = lib.splitString
        "mkManagedNode = pkgs: pkgs.buildEnv {"
        flakeSource;
    in
    if builtins.length definitionParts > 1
    then builtins.elemAt (lib.splitString "    };" (builtins.elemAt definitionParts 1)) 0
    else "";
  managedNodeSourceLines = lib.splitString "\n" managedNodeSource;
  managedGoPackages = [
    pkgs.go
    pkgs.gopls
    pkgs.golangci-lint
    pkgs.delve
    pkgs.gotools
  ];
  managedSystemPackages =
    flake.darwinConfigurations.example-primary.config.environment.systemPackages;
  unstablePkgs = flake.inputs.nixpkgs-unstable.legacyPackages.${system};
  managedHomebrewBrews = builtins.map
    (brew: brew.name)
    flake.darwinConfigurations.example-primary.config.homebrew.brews;
  moduleNames =
    builtins.filter
      (name: lib.hasSuffix ".nix" name)
      (builtins.attrNames (builtins.readDir (repoRoot + "/modules")));
  missingModuleImports =
    builtins.filter
      (name: builtins.match ".*\\(import \\./modules/${name}.*" flakeSource == null)
      moduleNames;
  retiredRepoMapInputs = [
    "repo-map-runner-src"
    "repo-map-manager-src"
    "repo-map-tools-src"
    "repo-map-orch33-tools-src"
  ];
  retiredRepoMapPackages = [
    "repo-map-runner"
    "repo-map-manager"
    "repo-map-orchestrator"
    "repo-map-orch32-contract"
    "repo-map-orch32-loopback-adapter"
    "repo-map-orch32-worker-rootfs"
    "repo-map-orch33-launchers"
  ];
  retiredRepoMapModules = [
    "repo-map-runner.nix"
    "repo-map-project-tools.nix"
    "repo-map-manager.nix"
    "repo-map-orchestrator.nix"
  ];
  retiredRepoMapSupportPaths = [
    "config/repo-map-orch32"
    "libexec/repo-map-runner"
    "packages/repo-map-runner"
    "packages/repo-map-manager"
    "packages/repo-map-orchestrator"
    "packages/repo-map-orch32-contract"
    "packages/repo-map-orch32-loopback-adapter"
    "packages/repo-map-orch32-worker-rootfs"
    "packages/repo-map-orch33-launchers"
  ];
  nixpkgsPolicy = import (repoRoot + "/modules/nixpkgs-policy.nix") { inherit lib; };
  allowUnfree = nixpkgsPolicy.nixpkgs.config.allowUnfreePredicate;
  mkPackage = name: { inherit name; };
in
{
  testDarwinConfigurationsExposeManagedHosts = {
    expr = builtins.sort builtins.lessThan (builtins.attrNames flake.darwinConfigurations);
    expected = [
      "example-primary"
      "example-workstation"
    ];
  };

  testLegacyPackagesExposeCurrentSystem = {
    expr = builtins.hasAttr system flake.legacyPackages;
    expected = true;
  };

  testCoreRetiresCustomTartProjection = {
    expr =
      let
        upstream = {
          tart = "upstream-tart-fixture";
          brave = "upstream-brave-fixture";
          stdenv.hostPlatform.isDarwin = true;
        };
        final.callPackage = source: arguments: { inherit source arguments; };
        # A fixed predecessor records declarations, including identity overrides.
        # This does not evaluate the final package-set fixed point.
        overlayResults = builtins.map
          (configuration: builtins.foldl'
            (result: overlay: result // (overlay final upstream))
            { }
            configuration.config.nixpkgs.overlays)
          (builtins.attrValues flake.darwinConfigurations);
      in
      {
        projectionSourceAbsent =
          !(builtins.pathExists (repoRoot + "/packages/tart-projected/default.nix"));
        projectionReferenceAbsent = !(lib.hasInfix "packages/tart-projected" flakeSource);
        upstreamTartAvailable = pkgs ? tart;
        overlaysDoNotDefineTart = builtins.all
          (result: !(builtins.hasAttr "tart" result))
          overlayResults;
        upstreamTartPassesThrough = builtins.all
          (result: (upstream // result).tart == upstream.tart)
          overlayResults;
        # Overlay paths belong to the resolved flake, which may be a copied source.
        signedBraveOverlayPreserved = builtins.all
          (result: toString result.brave.source == "${flake.outPath}/packages/brave-signed-dmg"
            && result.brave.arguments.brave == upstream.brave)
          overlayResults;
        rtkOverlayPreserved = builtins.all
          (result: toString result.rtk.source == "${flake.outPath}/packages/rtk")
          overlayResults;
      };
    expected = {
      projectionSourceAbsent = true;
      projectionReferenceAbsent = true;
      upstreamTartAvailable = true;
      overlaysDoNotDefineTart = true;
      upstreamTartPassesThrough = true;
      signedBraveOverlayPreserved = true;
      rtkOverlayPreserved = true;
    };
  };

  testCoreDoesNotInstallOrClaimTart = {
    expr =
      let
        configurations = builtins.attrValues flake.darwinConfigurations;
        applicationClaimsLib = import (repoRoot + "/lib/application-claims.nix") { inherit lib; };
      in
      {
        absentFromSystemProfiles = builtins.all
          (configuration: builtins.all
            (package: lib.getName package != "tart")
            configuration.config.environment.systemPackages)
          configurations;
        absentFromHomebrew = builtins.all
          (configuration: builtins.all
            (package: builtins.baseNameOf package.name != "tart")
            (configuration.config.homebrew.brews ++ configuration.config.homebrew.casks))
          configurations;
        noApplicationClaim = builtins.all
          (hostName: builtins.all
            (claim:
              let
                normalized = applicationClaimsLib.normalize { owner = "core"; inherit claim; };
              in
              builtins.baseNameOf normalized.normalizedPackage != "tart"
              && normalized.normalizedArtifact != "tart.app")
            (flake.applicationClaimsFor { inherit hostName; }))
          (builtins.attrNames flake.darwinConfigurations);
        noCorePackageOutput = !(builtins.hasAttr "tart" flake.packages.${system});
      };
    expected = {
      absentFromSystemProfiles = true;
      absentFromHomebrew = true;
      noApplicationClaim = true;
      noCorePackageOutput = true;
    };
  };

  testCoreFlakeRetiresLegacyRepoMapInputs = {
    expr = {
      flakeInputsAbsent = builtins.all
        (name: !(builtins.hasAttr name flake.inputs))
        retiredRepoMapInputs;
      lockNodesAbsent = builtins.all
        (name: !(builtins.hasAttr name flakeLock.nodes))
        retiredRepoMapInputs;
      sourceDeclarationsAbsent = builtins.all
        (name: !(lib.hasInfix name flakeSource))
        retiredRepoMapInputs;
    };
    expected = {
      flakeInputsAbsent = true;
      lockNodesAbsent = true;
      sourceDeclarationsAbsent = true;
    };
  };

  testCorePackageSetDoesNotExposeRetiredRepoMapPackages = {
    expr = {
      packageOutputsAbsent = builtins.all
        (name: !(builtins.hasAttr name flake.packages.${system}))
        retiredRepoMapPackages;
      legacyPackagesAbsent = builtins.all
        (name: !(builtins.hasAttr name pkgs))
        retiredRepoMapPackages;
    };
    expected = {
      packageOutputsAbsent = true;
      legacyPackagesAbsent = true;
    };
  };

  testCoreModulesAndSupportPathsRetireLegacyRepoMapDeployment = {
    expr = {
      moduleImportsAbsent = builtins.all
        (name: !(lib.hasInfix "./modules/${name}" flakeSource))
        retiredRepoMapModules;
      moduleFilesAbsent = builtins.all
        (name: !(builtins.pathExists (repoRoot + "/modules/${name}")))
        retiredRepoMapModules;
      supportPathsAbsent = builtins.all
        (path: !(builtins.pathExists (repoRoot + "/${path}")))
        retiredRepoMapSupportPaths;
    };
    expected = {
      moduleImportsAbsent = true;
      moduleFilesAbsent = true;
      supportPathsAbsent = true;
    };
  };

  testFlakeExposesNixUnitCheck = {
    expr = builtins.hasAttr "nix-unit-tests" flake.checks.${system};
    expected = true;
  };

  testFlakeExposesPythonTestRuntime = {
    expr = builtins.hasAttr "nixDarwinTestPython" flake.packages.${system};
    expected = true;
  };

  testFlakeExposesManagedGoToolEnvironment = {
    expr =
      let
        hasManagedGoTools =
          builtins.hasAttr "nixDarwinManagedGoTools" flake.packages.${system};
      in
      {
        package = hasManagedGoTools;
        packageName =
          if hasManagedGoTools
          then lib.getName flake.packages.${system}.nixDarwinManagedGoTools
          else null;
        usesBuildEnv = lib.hasInfix "nixDarwinManagedGoTools = pkgs.buildEnv" flakeSource;
        usesManagedToolCollection = lib.hasInfix "paths = mkManagedGoTools pkgs;" flakeSource;
      };
    expected = {
      package = true;
      packageName = "nix-darwin-managed-go-tools";
      usesBuildEnv = true;
      usesManagedToolCollection = true;
    };
  };

  testManagedGoToolCollectionUsesPinnedNixpkgsPackages = {
    expr = {
      go = builtins.elem "      pkgs.go" managedGoToolsSourceLines;
      gopls = builtins.elem "      pkgs.gopls" managedGoToolsSourceLines;
      golangciLint = builtins.elem "      pkgs.golangci-lint" managedGoToolsSourceLines;
      delve = builtins.elem "      pkgs.delve" managedGoToolsSourceLines;
      gotools = builtins.elem "      pkgs.gotools" managedGoToolsSourceLines;
      installedInSystem = builtins.all
        (package: builtins.elem package managedSystemPackages)
        managedGoPackages;
      absentFromHomebrew = builtins.all
        (name: !(builtins.elem name managedHomebrewBrews))
        [ "go" "gopls" "golangci-lint" "delve" "gotools" ];
      noExternalInstaller = builtins.all
        (installer: !(lib.hasInfix installer managedGoToolsSource))
        [ "go install" "brew" "curl" "npm" "pip" "asdf" "mise" "sdkman" ];
    };
    expected = {
      go = true;
      gopls = true;
      golangciLint = true;
      delve = true;
      gotools = true;
      installedInSystem = true;
      absentFromHomebrew = true;
      noExternalInstaller = true;
    };
  };

  testFlakeExposesManagedRubyEnvironment = {
    expr =
      let
        hasManagedRuby =
          builtins.hasAttr "nixDarwinManagedRuby" flake.packages.${system};
      in
      {
        package = hasManagedRuby;
        installedInSystem = hasManagedRuby
          && builtins.elem
            flake.packages.${system}.nixDarwinManagedRuby
            managedSystemPackages;
      };
    expected = {
      package = true;
      installedInSystem = true;
    };
  };

  testManagedRubyUsesPinnedNixpkgsPackages = {
    expr = {
      ruby = lib.hasInfix
        "mkManagedRuby = pkgs: pkgs.ruby.withPackages"
        flakeSource;
      bundler = builtins.elem "      pkgs.bundler" managedRubySourceLines;
      pry = builtins.elem "      rubyPackages.pry" managedRubySourceLines;
      rake = builtins.elem "      rubyPackages.rake" managedRubySourceLines;
      minitest = builtins.elem "      rubyPackages.minitest" managedRubySourceLines;
      rubyLsp = builtins.elem "      rubyPackages.ruby-lsp" managedRubySourceLines;
      rubygemsFromRuby =
        !(builtins.hasAttr "rubygems-update" pkgs.rubyPackages)
        && lib.hasInfix
          "mkManagedRuby = pkgs: pkgs.ruby.withPackages"
          flakeSource;
      absentFromHomebrew = builtins.all
        (name: !(builtins.elem name managedHomebrewBrews))
        [ "ruby" "pry" "bundler" "rake" "rubygems-update" "minitest" ];
      noExternalInstaller = builtins.all
        (installer: !(lib.hasInfix installer managedRubySource))
        [ "gem install" "brew" "curl" "npm" "pip" "asdf" "mise" "sdkman" ];
    };
    expected = {
      ruby = true;
      bundler = true;
      pry = true;
      rake = true;
      minitest = true;
      rubyLsp = true;
      rubygemsFromRuby = true;
      absentFromHomebrew = true;
      noExternalInstaller = true;
    };
  };

  testFlakeExposesManagedNodeEnvironment = {
    expr =
      let
        hasManagedNode =
          builtins.hasAttr "nixDarwinManagedNode" flake.packages.${system};
      in
      {
        package = hasManagedNode;
        packageName =
          if hasManagedNode
          then lib.getName flake.packages.${system}.nixDarwinManagedNode
          else null;
        installedInSystem = hasManagedNode
          && builtins.elem
            flake.packages.${system}.nixDarwinManagedNode
            managedSystemPackages;
      };
    expected = {
      package = true;
      packageName = "nix-darwin-managed-node";
      installedInSystem = true;
    };
  };

  testManagedNodeUsesPinnedNixpkgsPackages = {
    expr = {
      nodejs = builtins.elem "        pkgs.nodejs_22" managedNodeSourceLines;
      nodeMajor = lib.versions.major pkgs.nodejs_22.version;
      angularCli = builtins.elem
        "        pkgs.nodePackages.\"@angular/cli\""
        managedNodeSourceLines;
      nestCli = builtins.elem
        "        pkgs.nodePackages.\"@nestjs/cli\""
        managedNodeSourceLines;
      playwright = builtins.elem
        "        pkgs.playwright-test"
        managedNodeSourceLines;
      corepack = builtins.elem
        "        pkgs.corepack_22"
        managedNodeSourceLines;
      typescript = builtins.elem
        "        pkgs.typescript"
        managedNodeSourceLines;
      typescriptLanguageServer = builtins.elem
        "        pkgs.nodePackages.typescript-language-server"
        managedNodeSourceLines;
      tsx = builtins.elem
        "        pkgs.tsx"
        managedNodeSourceLines;
      dinasFrontendTools = builtins.elem
        "        pkgs.dinasFrontendTools"
        managedNodeSourceLines;
      packageLineCount = builtins.length
        (builtins.filter
          (line: lib.hasPrefix "        pkgs." line)
          managedNodeSourceLines);
      absentFromHomebrew = builtins.all
        (name: !(builtins.elem name managedHomebrewBrews))
        [ "node" "nodejs" "npm" "angular-cli" "nest-cli" "playwright" ];
      immutableCustomNpmBundle =
        lib.hasInfix "buildNpmPackage" frontendToolsSource
        && lib.hasInfix "npmDepsHash = \"sha256-" frontendToolsSource
        && lib.hasInfix "buildNpmPackage" nodeFixtureSource
        && lib.hasInfix "npmDepsHash =" nodeFixtureSource
        && !(lib.hasInfix "NODE_PATH" managedNodeSource)
        && !(lib.hasInfix "npm install" managedNodeSource)
        && !(lib.hasInfix "npx " managedNodeSource);
    };
    expected = {
      nodejs = true;
      nodeMajor = "22";
      angularCli = true;
      nestCli = true;
      playwright = true;
      corepack = true;
      typescript = true;
      typescriptLanguageServer = true;
      tsx = true;
      dinasFrontendTools = true;
      packageLineCount = 9;
      absentFromHomebrew = true;
      immutableCustomNpmBundle = true;
    };
  };

  testFlakeExposesArqRestorePackage = {
    expr = {
      package = builtins.hasAttr "arq_restore" flake.packages.${system};
      legacyPackage = builtins.hasAttr "arq_restore" pkgs;
      packageName = lib.getName flake.packages.${system}.arq_restore;
    };
    expected = {
      package = true;
      legacyPackage = true;
      packageName = "arq_restore";
    };
  };

  testFlakeExposesPinnedPublicRepoMapPackage = {
    expr = {
      inputDeclared = lib.hasInfix "repo-map-src" flakeSource;
      package = builtins.hasAttr "repomap-kg" flake.packages.${system};
      legacyPackage = builtins.hasAttr "repomap-kg" pkgs;
      packageName = lib.getName flake.packages.${system}.repomap-kg;
      packageUsesPinnedPublicInput = lib.hasInfix
        ''url = "github:Knowledge-Forge-AI/repo-map/1adfc7a15e830c8709f76f4e61abb9e24a8c0b17";''
        flakeSource;
      packageUsesLocalInput = lib.hasInfix "src = inputs.repo-map-src" flakeSource;
      packageBuildsPythonApplication = lib.hasInfix "buildPythonApplication" repomapKgSource;
      packageDeclaresPsycopgDependency = lib.hasInfix "python3Packages.psycopg" repomapKgSource;
      packagePinsSetuptools = lib.hasInfix
        ''url = "https://files.pythonhosted.org/packages/5d/40/e1e72872c6354b306daef1703549e8e83b4d43cfea356311bf722a043752/setuptools-83.0.0-py3-none-any.whl";''
        repomapKgSource
        && lib.hasInfix
          ''hash = "sha256-KbI8Ng8i9BTccza7OReMx7y/YCHtJzPN4XPwnboZq7M=";''
          repomapKgSource;
      packagePinsTypingExtensions = lib.hasInfix ''version = "4.16.0";'' repomapKgSource
        && lib.hasInfix
          ''hash = "sha256-3Jg9GaUJyU26ci7mq9M5QPfAWoniQ8R+kH60228aQ+U=";''
          repomapKgSource;
      packageAlignsPsycopgTypingExtensions = lib.hasInfix
        "typing-extensions = typingExtensions;"
        repomapKgSource;
      packageDeclaresCurrentRuntimeDependencies = builtins.all
        (dependency: builtins.elem "    ${dependency}" repomapKgDependencyLines)
        [ "psycopg" "setuptools" "typingExtensions" ];
      packageInstallsRuntimeDockerBuildContext = builtins.all
        (sourcePath: lib.hasInfix sourcePath repomapKgSource)
        [
          ''runtime_source_target="$out/lib"''
          ''pyproject.toml README.md "$runtime_source_target/"''
          ''src/main/go "$runtime_source_target/src/main/go"''
          ''src/main/python "$runtime_source_target/src/main/python"''
          ''src/main/resources "$runtime_source_target/src/main/resources"''
          ''-type d -name __pycache__ -prune -exec rm -r {} +''
        ];
      exposesConsoleScript = lib.hasInfix "repomap-kg" repomapKgSource;
    };
    expected = {
      inputDeclared = true;
      package = true;
      legacyPackage = true;
      packageName = "repomap-kg";
      packageUsesPinnedPublicInput = true;
      packageUsesLocalInput = true;
      packageBuildsPythonApplication = true;
      packageDeclaresPsycopgDependency = true;
      packagePinsSetuptools = true;
      packagePinsTypingExtensions = true;
      packageAlignsPsycopgTypingExtensions = true;
      packageDeclaresCurrentRuntimeDependencies = true;
      packageInstallsRuntimeDockerBuildContext = true;
      exposesConsoleScript = true;
    };
  };

  testArqRestoreCarriesArq7TreeV4Patch = {
    expr = {
      patchListed = lib.hasInfix "arq7-tree-v4-node-fields.patch" arqRestoreSource;
      patchExists = builtins.pathExists arqRestoreTreeV4Patch;
      readsAddedTime = lib.hasInfix "addedTime_sec" arqRestoreTreeV4PatchSource;
      readsDocumentId = lib.hasInfix "documentId" arqRestoreTreeV4PatchSource;
      readsSparseMetadata = lib.hasInfix "sparseLogicalSize" arqRestoreTreeV4PatchSource
        && lib.hasInfix "holeCount" arqRestoreTreeV4PatchSource;
    };
    expected = {
      patchListed = true;
      patchExists = true;
      readsAddedTime = true;
      readsDocumentId = true;
      readsSparseMetadata = true;
    };
  };

  testManagedPythonRuntimeIncludesWorkstationPackages = {
    expr = {
      coverage = lib.hasInfix "pythonPackages.coverage" managedPythonSource;
      pillow = lib.hasInfix "pythonPackages.pillow" managedPythonSource;
      pip = lib.hasInfix "pythonPackages.pip" managedPythonSource;
      psycopg = lib.hasInfix "pythonPackages.psycopg" managedPythonSource;
      psycopgV3 = lib.versionAtLeast pkgs.python3Packages.psycopg.version "3";
      pytest = lib.hasInfix "pythonPackages.pytest" managedPythonSource;
      pyyaml = lib.hasInfix "pythonPackages.pyyaml" managedPythonSource;
      pypistats = lib.hasInfix "pkgs.pypistats" managedPythonSource;
      testPythonDoesNotIncludePillow = lib.hasInfix "pythonPackages.pillow" testPythonSource;
      hasPillowCheck = builtins.hasAttr "managed-python-pillow" flake.checks.${system};
      hasPypistatsCheck = builtins.hasAttr "managed-python-pypistats" flake.checks.${system};
    };
    expected = {
      coverage = true;
      pillow = true;
      pip = true;
      psycopg = true;
      psycopgV3 = true;
      pytest = true;
      pyyaml = true;
      pypistats = true;
      testPythonDoesNotIncludePillow = false;
      hasPillowCheck = true;
      hasPypistatsCheck = true;
    };
  };

  testManagedRustUsesPinnedNixpkgsPackages = {
    expr = {
      rustPackage = builtins.hasAttr "nixDarwinManagedRust" flake.packages.${system};
      hasRustCheck = builtins.hasAttr "managed-rust-tools" flake.checks.${system};
      hasNodeCheck = builtins.hasAttr "managed-node-tools" flake.checks.${system};
      tauriMajor = lib.versions.major pkgs.cargo-tauri.version;
    };
    expected = {
      rustPackage = true;
      hasRustCheck = true;
      hasNodeCheck = true;
      tauriMajor = "2";
    };
  };

  testPypistatsUsesCompatibleExactTermcolor = {
    expr = {
      pypistatsVersion = pkgs.pypistats.version;
      termcolorVersion = pkgs.termcolor_3_2.version;
      noRelaxation = !(lib.hasInfix "pythonRelaxDeps" pypistatsSource);
    };
    expected = {
      pypistatsVersion = "1.14.0";
      termcolorVersion = "3.2.0";
      noRelaxation = true;
    };
  };

  testManagedTestRuntimesIncludeCoverageTools = {
    expr = {
      pytestCov = lib.hasInfix "pythonPackages.pytest-cov" flakeSource;
      pytestXdist = lib.hasInfix "pythonPackages.pytest-xdist" flakeSource;
      miniTest = lib.hasInfix "rubyPackages.minitest" flakeSource;
      simpleCov = lib.hasInfix "rubyPackages.simplecov" flakeSource;
      zunit = lib.hasInfix "pkgs.zunit" flakeSource;
      testRubyPackage = builtins.hasAttr "nixDarwinTestRuby" flake.packages.${system};
    };
    expected = {
      pytestCov = true;
      pytestXdist = true;
      miniTest = true;
      simpleCov = true;
      zunit = true;
      testRubyPackage = true;
    };
  };

  testManagedPackagesIncludeTabularDataTools = {
    expr = {
      visidata = lib.hasInfix "pkgs.visidata" flakeSource;
      dasel = lib.hasInfix "pkgs.dasel" flakeSource;
      daselInstalled = builtins.elem pkgs.dasel managedSystemPackages;
    };
    expected = {
      visidata = true;
      dasel = true;
      daselInstalled = true;
    };
  };

  testManagedPackagesIncludeTmuxAndSensiblePlugin = {
    expr = {
      tmux = builtins.elem pkgs.tmux managedSystemPackages;
      sensible = builtins.elem pkgs.tmuxPlugins.sensible managedSystemPackages;
      pluginPathLinked = builtins.elem
        "/share/tmux-plugins"
        flake.darwinConfigurations.example-primary.config.environment.pathsToLink;
    };
    expected = {
      tmux = true;
      sensible = true;
      pluginPathLinked = true;
    };
  };

  testManagedPackagesIncludeBetterleaks = {
    expr = {
      input = builtins.hasAttr "nixpkgs-unstable" flake.inputs;
      sourceDeclaration = lib.hasInfix "unstablePkgs.betterleaks" flakeSource;
      systemPackage = builtins.elem unstablePkgs.betterleaks managedSystemPackages;
    };
    expected = {
      input = true;
      sourceDeclaration = true;
      systemPackage = true;
    };
  };

  testRtkIsPinnedAndExposedThroughTheFlake = {
    expr = {
      overlayPackage = lib.hasInfix
        "rtk = final.callPackage ./packages/rtk { };"
        flakeSource;
      packageOutput = builtins.hasAttr "rtk" flake.packages.${system};
      systemPackage = builtins.elem pkgs.rtk managedSystemPackages;
      version = lib.hasInfix ''version = "0.43.0";'' rtkSource;
      sourceHash = lib.hasInfix
        ''hash = "sha256-n5bkPPsrdM4fE5ltocTjlq+JwRgp39yib6S79fci4m4=";''
        rtkSource;
      cargoHash = lib.hasInfix
        ''cargoHash = "sha256-XKUKdhxfnwUCOx9slqx4oUFa09HcosPLVh5Xkh87oSk=";''
        rtkSource;
      apacheLicense = lib.hasInfix "license = lib.licenses.asl20;" rtkSource;
    };
    expected = {
      overlayPackage = true;
      packageOutput = true;
      systemPackage = true;
      version = true;
      sourceHash = true;
      cargoHash = true;
      apacheLicense = true;
    };
  };

  testEveryModuleFileIsImportedByTheFlake = {
    expr = missingModuleImports;
    expected = [ ];
  };

  testCoreExportsReusableCompositionInterface = {
    expr = {
      coreModule = builtins.hasAttr "core" flake.darwinModules;
      hosts = builtins.attrNames flake.lib.hosts;
      constructor = builtins.isFunction flake.lib.mkDarwinConfiguration;
      darwinSystemCallCount =
        builtins.length (lib.splitString "nix-darwin.lib.darwinSystem" flakeSource) - 1;
    };
    expected = {
      coreModule = true;
      hosts = [ "example-primary" "example-workstation" ];
      constructor = true;
      darwinSystemCallCount = 1;
    };
  };

  testCoreConstructorAppendsExtraModules = {
    expr =
      let
        configuration = flake.lib.mkDarwinConfiguration {
          hostName = "example-primary";
          extraModules = [
            { environment.variables.COMPOSITION_EXTRA_MODULE = "present"; }
          ];
        };
      in
      {
        extraModule = configuration.config.environment.variables.COMPOSITION_EXTRA_MODULE;
        existingHost = configuration.config.networking.hostName;
      };
    expected = {
      extraModule = "present";
      existingHost = "example-primary";
    };
  };

  testCoreConstructorRejectsUnknownHostFacts = {
    expr = (builtins.tryEval (flake.lib.mkDarwinConfiguration {
      hostName = "example-primary";
      hostConfig = {
        nixbldBaseUid = 350;
        nixbldGroupGid = 350;
        unknownModuleFlag = true;
      };
    })).success;
    expected = false;
  };

  testCoreExportsTruthfulOverlayAndPackageSetIdentity = {
    expr =
      let
        hasDefaultOverlay = builtins.hasAttr "overlays" flake
          && builtins.hasAttr "default" flake.overlays;
        hasIdentity = builtins.hasAttr "packageSetIdentity" flake.lib;
        identity = if hasIdentity then flake.lib.packageSetIdentity else { };
      in
      {
        inherit hasDefaultOverlay hasIdentity;
        exportedOverlayAttrs =
          if hasDefaultOverlay
          then builtins.attrNames (flake.overlays.default pkgs pkgs)
          else [ "missing" ];
        identityFields = builtins.attrNames identity;
        identitySchema = identity.schema or "missing";
        revisionMatches = (identity.revision or null) == flake.inputs.nixpkgs.rev;
        outPathMatches = (identity.outPath or null) == builtins.toString flake.inputs.nixpkgs.outPath;
      };
    expected = {
      hasDefaultOverlay = true;
      hasIdentity = true;
      exportedOverlayAttrs = [ ];
      identityFields = [ "outPath" "revision" "schema" ];
      identitySchema = "nix-darwin-package-set-v1";
      revisionMatches = true;
      outPathMatches = true;
    };
  };

  testCoreConstructorOrdersExtraOverlaysAfterGrandfatheredOverlay = {
    expr =
      let
        configuration = flake.lib.mkDarwinConfiguration {
          hostName = "example-primary";
          extraOverlays = [
            (_final: prev: {
              nd2SeesGrandfatheredOverlay = builtins.hasAttr "rtk" prev;
              nd2OverlayOrder = [ "first" ];
            })
            (_final: prev: {
              nd2OverlayOrder = prev.nd2OverlayOrder ++ [ "second" ];
            })
          ];
        };
      in
      {
        seesGrandfatheredOverlay = configuration.pkgs.nd2SeesGrandfatheredOverlay;
        order = configuration.pkgs.nd2OverlayOrder;
      };
    expected = {
      seesGrandfatheredOverlay = true;
      order = [ "first" "second" ];
    };
  };

  testCoreDeclaresSafeLocalConfigDefault = {
    expr =
      let
        defaultFlake = import (repoRoot + "/config/nix-darwin/default/flake.nix");
      in
      {
        inputDeclared = builtins.hasAttr "localConfig" flake.inputs;
        inputIsFlake = builtins.hasAttr "outPath" flake.inputs.localConfig;
        allowlist = flake.configAllowlist;
        trackedDefault = lib.hasInfix
          ''localConfig.url = "path:./config/nix-darwin/default";''
          flakeSource;
        outputs = defaultFlake.outputs {
          self = defaultFlake // {
            outPath = "/var/empty/machine-local";
            darwinConfigurations.synthetic.config.machineLocal = true;
          };
        };
        defaultOutputs = defaultFlake.outputs {
          self = defaultFlake;
        };
      };
    expected = {
      inputDeclared = true;
      inputIsFlake = true;
      allowlist = {
        "kitty-bash.toml" = [
          "schema"
          "palette"
          "terminal"
          "completion"
          "ansi?"
          "ble.faces?"
        ];
      };
      trackedDefault = true;
      outputs = { };
      defaultOutputs = { };
    };
  };

  testDeveloperToolingRemainsCoreOwned = {
    expr = {
      moduleExists = builtins.pathExists developerToolingModule;
      importedByCore = lib.hasInfix
        "(import ./modules/developer-tooling.nix)"
        flakeSource;
      ownsAllPackages = builtins.all
        (package: lib.hasInfix "pkgs.${package}" developerToolingSource)
        [
          "liquibase"
          "nil"
          "nixd"
          "nixfmt-rfc-style"
          "ripgrep"
          "shellcheck"
          "shfmt"
        ];
    };
    expected = {
      moduleExists = true;
      importedByCore = true;
      ownsAllPackages = true;
    };
  };

  testDeveloperToolingDeclaresSourceAuditTools = {
    expr =
      let
        configurations = builtins.attrValues flake.darwinConfigurations;
        applicationClaimsLib = import (repoRoot + "/lib/application-claims.nix") { inherit lib; };
        auditTools = [ "malskanner" "semgrep" ];
        countOf = predicate: list: builtins.length (builtins.filter predicate list);
        priorBrews = [ "kcov" "mas" "nvm" "rbenv" "ruby-build" ];
        priorPackages = [ "liquibase" "nil" "nixd" "nixfmt-rfc-style" "ripgrep" "shellcheck" "shfmt" ];
        packageSource = repoRoot + "/packages/malskanner";
        malskannerSource = builtins.readFile (packageSource + "/default.nix");
        wrapperManifest = builtins.fromJSON (builtins.readFile (packageSource + "/package.json"));
        lockPackages = removeAttrs
          (builtins.fromJSON (builtins.readFile (packageSource + "/package-lock.json"))).packages
          [ "" ];
        lockEntries = builtins.attrValues lockPackages;
        upstreamEntry = lockPackages."node_modules/malskanner";
        package = flake.packages.${system}.malskanner;
        final.callPackage = source: arguments: { inherit source arguments; };
        overlayResults = builtins.map
          (configuration: builtins.foldl'
            (result: overlay: result // (overlay final {
              brave = "upstream-brave-fixture";
              stdenv.hostPlatform.isDarwin = true;
            }))
            { }
            configuration.config.nixpkgs.overlays)
          configurations;
      in
      {
        oneSemgrepBrew = builtins.all
          (configuration: countOf (brew: brew.name == "semgrep") configuration.config.homebrew.brews == 1)
          configurations;
        priorBrewsPreserved = builtins.all
          (configuration: builtins.all
            (name: builtins.elem name (builtins.map (brew: brew.name) configuration.config.homebrew.brews))
            priorBrews)
          configurations;
        noNixSemgrep = builtins.all
          (configuration: !(builtins.any
            (package: lib.getName package == "semgrep")
            configuration.config.environment.systemPackages))
          configurations;
        oneMalskannerPackage = builtins.all
          (configuration: countOf
            (package: package == configuration.pkgs.malskanner)
            configuration.config.environment.systemPackages == 1)
          configurations;
        priorPackagesPreserved = builtins.all
          (configuration: builtins.all
            (name: builtins.elem configuration.pkgs.${name} configuration.config.environment.systemPackages)
            priorPackages)
          configurations;
        noApplicationClaims = builtins.all
          (hostName: builtins.all
            (claim:
              let
                normalized = applicationClaimsLib.normalize { owner = "core"; inherit claim; };
              in
              !(builtins.elem (builtins.baseNameOf normalized.normalizedPackage) auditTools))
            (flake.applicationClaimsFor { inherit hostName; }))
          (builtins.attrNames flake.darwinConfigurations);
        noLaunchdServices = builtins.all
          (configuration:
            let
              launchd = configuration.config.launchd;
              names = builtins.attrNames launchd.agents
                ++ builtins.attrNames launchd.daemons
                ++ builtins.attrNames launchd.user.agents;
            in
            !(builtins.any (name: builtins.any (tool: lib.hasInfix tool name) auditTools) names))
          configurations;
        overlayDeclaresPackage = builtins.all
          (result: toString result.malskanner.source == "${flake.outPath}/packages/malskanner"
            && result.malskanner.arguments == { })
          overlayResults;
        packageVersion = package.version;
        packageMainProgram = package.meta.mainProgram;
        packageLicense = package.meta.license.spdxId;
        exactDirectDependency = wrapperManifest.dependencies;
        upstreamIntegrity = upstreamEntry.integrity;
        upstreamResolved = upstreamEntry.resolved;
        allTransitivesRegistryPinned = builtins.all
          (entry: lib.hasPrefix "https://registry.npmjs.org/" entry.resolved
            && lib.hasPrefix "sha512-" entry.integrity)
          lockEntries;
        noInstallScripts = !(builtins.any (entry: entry.hasInstallScript or false) lockEntries);
        realDependencyHash = builtins.match
          ".*npmDepsHash = \"sha256-[A-Za-z0-9+/]{43}=\";.*"
          (builtins.replaceStrings [ "\n" ] [ " " ] malskannerSource) != null;
        noFakeHash = !(lib.hasInfix "fakeHash" malskannerSource || lib.hasInfix "fakeSha256" malskannerSource);
        scriptsIgnored = lib.hasInfix "\"--ignore-scripts\"" malskannerSource;
        notInManagedNode = !(lib.hasInfix "malskanner" managedNodeSource);
      };
    expected = {
      oneSemgrepBrew = true;
      priorBrewsPreserved = true;
      noNixSemgrep = true;
      oneMalskannerPackage = true;
      priorPackagesPreserved = true;
      noApplicationClaims = true;
      noLaunchdServices = true;
      overlayDeclaresPackage = true;
      packageVersion = "0.1.3";
      packageMainProgram = "malskanner";
      packageLicense = "MIT";
      exactDirectDependency = { malskanner = "0.1.3"; };
      upstreamIntegrity =
        "sha512-9PmyOU/PmVUu4ZVj9lOZwTSxfYflGKAs9pC5y90DBkMJ5Vk41IOcNPRnfQlwKezCaED19GbwUsv9BE+x161Jcw==";
      upstreamResolved = "https://registry.npmjs.org/malskanner/-/malskanner-0.1.3.tgz";
      allTransitivesRegistryPinned = true;
      noInstallScripts = true;
      realDependencyHash = true;
      noFakeHash = true;
      scriptsIgnored = true;
      notInManagedNode = true;
    };
  };

  testNixpkgsPolicyAllowsOnlyExplicitUnfreePackages = {
    expr = builtins.map allowUnfree [
      (mkPackage "google-chrome")
      (mkPackage "ngrok")
      (mkPackage "softnet")
      (mkPackage "tart")
      (mkPackage "steam")
      (mkPackage "claude-code")
      (mkPackage "codex")
      (mkPackage "antigravity-cli")
      (mkPackage "agent-clis")
    ];
    expected = [
      true
      true
      true
      true
      false
      true
      true
      true
      true
    ];
  };

  testDeploymentEffectsOptionDefined = {
    expr =
      let
        configuration = flake.darwinConfigurations.example-workstation.config;
      in
      {
        hasEffects = builtins.hasAttr "deploymentEffects" configuration.dinas;
        schema = configuration.dinas.deploymentEffects.schema;
      };
    expected = {
      hasEffects = true;
      schema = "nix-darwin-deployment-effects-v1";
    };
  };

  testKittyStockBashEnabledOnlyOnTargetHost = {
    expr = {
      macbookStockEnabled = flake.darwinConfigurations.example-primary.config.dinas.kittyStockBash.enable;
      macbookDirenvEnabled = flake.darwinConfigurations.example-primary.config.dinas.kittyStockBash.direnv.enable;
      laptopStockEnabled = flake.darwinConfigurations.example-workstation.config.dinas.kittyStockBash.enable;
      laptopDirenvEnabled = flake.darwinConfigurations.example-workstation.config.dinas.kittyStockBash.direnv.enable;
      rolesR3Inactive = flake.lib.browserRolesR3Selected;
      macbookRolesR3 = flake.darwinConfigurations.example-primary.config.programs.browserBaseline.rolesR3;
      laptopRolesR3 = flake.darwinConfigurations.example-workstation.config.programs.browserBaseline.rolesR3;
    };
    expected = {
      macbookStockEnabled = true;
      macbookDirenvEnabled = false;
      laptopStockEnabled = false;
      laptopDirenvEnabled = false;
      rolesR3Inactive = false;
      macbookRolesR3 = false;
      laptopRolesR3 = false;
    };
  };

  testKittyStockBashInstalledStylePreservedOnTargetHost = {
    expr =
      let
        style = flake.darwinConfigurations.example-primary.config.dinas.kittyStockBash.installedStyle;
      in
      {
        schema = style.schema;
        pathColor = style.palette.path;
        fgColor = style.terminal.foreground;
        completionStyle = style.completion.style;
        hasBleFaces = style.ble.faces ? auto_complete;
      };
    expected = {
      schema = 1;
      pathColor = "#ffd700";
      fgColor = "#33ff00";
      completionStyle = "align-nowrap";
      hasBleFaces = true;
    };
  };
}
