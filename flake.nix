{
  description = "darwin-is-not-a-snowflake (DINAS)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-25.11-darwin";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    nix-darwin.url = "github:nix-darwin/nix-darwin/nix-darwin-25.11";
    nix-darwin.inputs.nixpkgs.follows = "nixpkgs";
    localConfig.url = "path:./config/nix-darwin/default";
    repo-map-src = {
      url = "github:Knowledge-Forge-AI/repo-map/1adfc7a15e830c8709f76f4e61abb9e24a8c0b17";
      flake = false;
    };
  };

  outputs = inputs@{ self, nix-darwin, nixpkgs, nixpkgs-unstable, ... }:
  let
    userName = "operator";
    system = "aarch64-darwin";
    unstablePkgs = nixpkgs-unstable.legacyPackages.${system};
    defaultHostName = "example-workstation";
    mkManagedPython = pkgs: pkgs.python3.withPackages (pythonPackages: [
      pythonPackages.coverage
      # Workstation image-processing support.
      pythonPackages.pillow
      pkgs.pypistats
      pythonPackages.pip
      pythonPackages.playwright
      pythonPackages.psycopg
      pythonPackages.pytest
      # Workstation automation and skill validation YAML processing support.
      pythonPackages.pyyaml
    ]);
    mkManagedGoTools = pkgs: [
      pkgs.go
      pkgs.gopls
      pkgs.golangci-lint
      pkgs.delve
      pkgs.gotools
    ];
    mkManagedRuby = pkgs: pkgs.ruby.withPackages (rubyPackages: [
      pkgs.bundler
      rubyPackages.pry
      rubyPackages.rake
      # pkgs.ruby supplies RubyGems, the rubygems-update payload.
      rubyPackages.minitest
      rubyPackages.ruby-lsp
    ]);
    mkManagedNode = pkgs: pkgs.buildEnv {
      name = "nix-darwin-managed-node";
      paths = [
        pkgs.nodejs_22
        pkgs.corepack_22
        pkgs.typescript
        pkgs.nodePackages.typescript-language-server
        pkgs.tsx
        pkgs.dinasFrontendTools
        pkgs.nodePackages."@angular/cli"
        pkgs.nodePackages."@nestjs/cli"
        pkgs.playwright-test
      ];
    };
    mkManagedRust = pkgs: pkgs.buildEnv {
      name = "nix-darwin-managed-rust";
      paths = [
        pkgs.rustc
        pkgs.cargo
        pkgs.rustfmt
        pkgs.clippy
        pkgs.rust-analyzer
        pkgs.pkg-config
        pkgs.cargo-tauri
      ];
    };
    mkTestPython = pkgs: pkgs.python3.withPackages (pythonPackages: [
      pythonPackages.pytest
      pythonPackages.pytest-cov
      pythonPackages.pytest-json-report
      pythonPackages.pytest-xdist
      # Keep test/report tooling aligned with the managed Python runtime.
      pythonPackages.pyyaml
    ]);
    mkTestRuby = pkgs: pkgs.ruby.withPackages (rubyPackages: [
      rubyPackages.minitest
      rubyPackages.simplecov
    ]);
    gitGuiSource = ./libexec/git-gui/git-gui;
    nixUnitTestRunnerSource = ./libexec/nix-unit-tests/run;
    coreOverlay = _final: _prev: { };
    localOverlay = final: _prev: {
      arq_restore = final.callPackage ./packages/arq_restore { };
      brave = if _prev.stdenv.hostPlatform.isDarwin then
        final.callPackage ./packages/brave-signed-dmg {
          inherit (_prev) brave;
        }
      else _prev.brave;
      rtk = final.callPackage ./packages/rtk { };
      repomap-kg = final.callPackage ./packages/repomap-kg {
        src = inputs.repo-map-src;
      };
      termcolor_3_2 = final.callPackage ./packages/termcolor { };
      pypistats = final.callPackage ./packages/pypistats {
        termcolor = final.termcolor_3_2;
      };
      dinasCommands = final.callPackage ./packages/dinas-commands { };
      dinasFrontendTools = final.callPackage ./packages/dinas-frontend-tools { };
      nodeBootstrapFixture = final.callPackage ./packages/node-bootstrap-fixture { };
      malskanner = final.callPackage ./packages/malskanner { };
    };
    hosts = {
      example-workstation = {
        nixbldBaseUid = 350;
        nixbldGroupGid = 350;
      };
      example-primary = {
        nixbldBaseUid = 350;
        nixbldGroupGid = 350;
      };
    };
    packageSetIdentity = {
      schema = "nix-darwin-package-set-v1";
      revision = nixpkgs.rev;
      outPath = builtins.toString nixpkgs.outPath;
    };

    applicationClaimsLib = import ./lib/application-claims.nix {
      inherit (nixpkgs) lib;
    };
    coreApplicationInventory = import ./lib/core-application-inventory.nix;
    coreApplicationClaims =
      let
        claims = builtins.map
          (entry: builtins.removeAttrs entry coreApplicationInventory.provenanceFields)
          coreApplicationInventory.guiApplications;
      in
      applicationClaimsLib.assertValid
        (applicationClaimsLib.attachOwner "core" claims)
        claims;

    # Inactive candidate: changing this literal requires a reviewed source release.
    # No runtime environment or module override can qualify the R3 rollout.
    browserRolesR3Selected = false;
    mkConfiguration = hostName: hostConfig: extraOverlays: { config, pkgs, ... }: {

      programs.browserBaseline.corePolicyOwnership = browserRolesR3Selected && hostName == "example-primary";
      programs.browserBaseline.rolesR3 = browserRolesR3Selected && hostName == "example-primary";
      programs.browserBaseline.stockApplications = hostName == "example-primary";
      dinas.kittyStockBash.enable = hostName == "example-primary";
      assertions = [{
        assertion = config.programs.browserBaseline.corePolicyOwnership == (browserRolesR3Selected && hostName == "example-primary")
          && config.programs.browserBaseline.rolesR3 == (browserRolesR3Selected && hostName == "example-primary")
          && config.programs.browserBaseline.stockApplications == (hostName == "example-primary");
        message = "The host constructor selects target stock applications and preserves inactive R3 policy; R3 selection requires a reviewed source change.";
      }];

      networking.hostName = hostName;
      networking.computerName = hostName;
      system.primaryUser = userName;
      users.users.${userName}.home = "/Users/${userName}";

      # List packages installed in system profile. To search by name, run:
      # $ nix-env -qaP | grep wget
      environment.systemPackages =
        let
          gitGui = pkgs.writeTextFile {
            name = "git-gui";
            destination = "/bin/git-gui";
            executable = true;
            text = builtins.replaceStrings
              [ "@GIT_GUI@" ]
              [ "${pkgs.gitFull}/libexec/git-core/git-gui" ]
              (builtins.readFile gitGuiSource);
          };
          managedPython = mkManagedPython pkgs;
          managedGoTools = mkManagedGoTools pkgs;
          managedRuby = mkManagedRuby pkgs;
          managedNode = mkManagedNode pkgs;
          managedRust = mkManagedRust pkgs;
        in
        [
          pkgs.vim
          pkgs.nmap
          pkgs.gitFull
          gitGui
          pkgs.gh
          pkgs.bash
          pkgs.bats
          pkgs.nix-unit
          pkgs.zunit
          pkgs.imagemagick
          pkgs.ngrok
          # Retained SQL tooling: no evidence authorizes removing its global
          # developer use during a storage ownership transfer.
          pkgs.postgresql
          pkgs.postgresql.dev
          pkgs.postgresql.pg_config
          pkgs.rtk
          pkgs.tmux
          pkgs.tmuxPlugins.sensible
          pkgs.visidata
          pkgs.playwright
          pkgs.playwright-driver
          pkgs.python3Packages.pytest
          pkgs.python3Packages.playwright
          pkgs.repomap-kg
          pkgs.dasel
          unstablePkgs.betterleaks
          managedPython
          managedRuby
          managedNode
          managedRust
        ]
        ++ managedGoTools;

      environment.pathsToLink = [ "/share/tmux-plugins" ];

      homebrew.enable = true;
      homebrew.casks = [
        "orbstack"
      ];

      # nix.package = pkgs.nix;

      # Necessary for using flakes on this system.
      nix.settings.experimental-features = [ "nix-command" "flakes" ];

      # Create /etc/zshrc that loads the nix-darwin environment.
      programs.zsh.enable = true;  # default shell on catalina
      # programs.fish.enable = true;

      # Set Git commit hash for darwin-version.
      system.configurationRevision = self.rev or self.dirtyRev or null;

      # Used for backwards compatibility, please read the changelog before changing.
      # $ darwin-rebuild changelog
      system.stateVersion = 4;
      nix.enable = true;
      # nix-darwin checks _nixbld1 against ids.uids.nixbld + 1.
      ids.uids.nixbld = hostConfig.nixbldBaseUid;
      ids.gids.nixbld = hostConfig.nixbldGroupGid;

      # The platform the configuration will be used on.
      nixpkgs.hostPlatform = system;
      nixpkgs.overlays = [ coreOverlay localOverlay ] ++ extraOverlays;
    };

    coreModule = {
      imports = [
        (import ./modules/deployment-effects.nix)
        (import ./modules/nixpkgs-policy.nix)
        (import ./modules/host-security.nix)
        (import ./modules/ssh.nix)
        (import ./modules/shell.nix {
          inherit userName;
          inherit (inputs) localConfig;
        })
        (import ./modules/terminal.nix { inherit userName; })
        (import ./modules/kitty.nix {
          inherit userName;
          inherit (inputs) localConfig;
        })
        (import ./modules/fastfetch.nix { inherit userName; })
        (import ./modules/codex.nix)
        (import ./modules/codex-state.nix { inherit (inputs) localConfig; })
        (import ./modules/android.nix)
        (import ./modules/brewfile-stragglers.nix)
        (import ./modules/oh-my-zsh.nix { inherit userName; })
        (import ./modules/sdkman.nix { inherit userName; })
        (import ./modules/nvm.nix { inherit userName; })
        (import ./modules/pyenv.nix { inherit userName; })
        (import ./modules/rbenv.nix { inherit userName; })
        (import ./modules/password-sync.nix)
        (import ./modules/virtual-machines.nix)
        (import ./modules/browsers.nix)
        (import ./modules/desktop-apps.nix)
        (import ./modules/developer-tooling.nix)
        (import ./modules/video-session.nix)
        (import ./modules/brave-youtube.nix { inherit userName; })
        (import ./modules/dinas/command-namespaces.nix {
          inherit (inputs) localConfig;
        })
      ];
    };
    mkDarwinConfiguration =
      {
        hostName,
        hostConfig ? hosts.${hostName},
        extraOverlays ? [ ],
        extraModules ? [ ],
      }:
      assert builtins.attrNames hostConfig == [
        "nixbldBaseUid"
        "nixbldGroupGid"
      ];
      nix-darwin.lib.darwinSystem {
        modules = [
          coreModule
          (mkConfiguration hostName hostConfig extraOverlays)
        ] ++ extraModules;
      };
  in
  {
    # Build darwin flake using:
    # $ darwin-rebuild build --flake .#example-workstation
    # $ darwin-rebuild build --flake .#example-primary
    darwinModules.core = coreModule;
    overlays.default = coreOverlay;

    # Top-level compositions aggregate module-owned allowlists here. A trailing
    # '?' admits an optional key while preserving the filename/list contract.
    configAllowlist = {
      "kitty-bash.toml" = [
        "schema"
        "palette"
        "terminal"
        "completion"
        "ansi?"
        "ble.faces?"
      ];
    };

    # The applications core still installs. A grandfathered core footprint has
    # to be declared for a destination module to hand off against it.
    applicationClaims = coreApplicationClaims;
    applicationClaimsFor = { hostName, system ? "aarch64-darwin" }:
      let
        entries = coreApplicationInventory.guiApplicationsFor {
          inherit hostName system;
          rolesR3 = browserRolesR3Selected && hostName == "example-primary";
          stockApplications = hostName == "example-primary";
        };
        claims = map (entry: builtins.removeAttrs entry coreApplicationInventory.provenanceFields) entries;
      in applicationClaimsLib.assertValid (applicationClaimsLib.attachOwner "core" claims) claims;

    lib = {
      inherit hosts mkDarwinConfiguration packageSetIdentity browserRolesR3Selected;
      storageOfficeOwnershipVersion = 1;
      userHomeWriterCommon = ./modules/user-home-writer-common.sh;
      coreApplicationInventoryFor = coreApplicationInventory.guiApplicationsFor;
      applicationClaims = applicationClaimsLib;
      coreApplicationInventory = coreApplicationInventory.guiApplications;
      nonApplicationCasks = coreApplicationInventory.nonApplicationCasks;
    };

    darwinConfigurations = nixpkgs.lib.mapAttrs
      (hostName: hostConfig: mkDarwinConfiguration {
        inherit hostName hostConfig;
      })
      hosts;

    # Expose the package set, including overlays, for convenience.
    legacyPackages.${system} = self.darwinConfigurations.${defaultHostName}.pkgs;

    packages.${system} =
      let
        pkgs = self.legacyPackages.${system};
      in
      {
        arq_restore = pkgs.arq_restore;
        nixDarwinManagedGoTools = pkgs.buildEnv {
          name = "nix-darwin-managed-go-tools";
          paths = mkManagedGoTools pkgs;
        };
        nixDarwinManagedPython = mkManagedPython pkgs;
        nixDarwinManagedRuby = mkManagedRuby pkgs;
        nixDarwinManagedNode = mkManagedNode pkgs;
        nixDarwinManagedRust = mkManagedRust pkgs;
        nixDarwinTestPython = mkTestPython pkgs;
        nixDarwinTestRuby = mkTestRuby pkgs;
        dinasCommands = pkgs.dinasCommands;
        dinasFrontendTools = pkgs.dinasFrontendTools;
        nodeBootstrapFixture = pkgs.nodeBootstrapFixture;
        malskanner = pkgs.malskanner;
        rtk = pkgs.rtk;
        repomap-kg = pkgs.repomap-kg;
      };

    checks.${system} =
      let
        pkgs = self.legacyPackages.${system};
        nixUnitTestRunner = pkgs.writeTextFile {
          name = "nix-darwin-nix-unit-test-runner";
          executable = true;
          text = builtins.replaceStrings
            [ "@NIX_UNIT@" ]
            [ "${pkgs.nix-unit}/bin/nix-unit" ]
            (builtins.readFile nixUnitTestRunnerSource);
        };
        python = self.packages.${system}.nixDarwinManagedPython;
        node = self.packages.${system}.nixDarwinManagedNode;
        rust = self.packages.${system}.nixDarwinManagedRust;
        shellConfig = self.darwinConfigurations.${defaultHostName}.config.system.build.dinasShellConfig;
      in
      {
        composed-command-namespace =
          let
            extra = pkgs.runCommandLocal "namespace-extra-command-fixture" { } ''
              mkdir -p "$out/bin" "$out/libexec/storage"
              printf '#!/bin/sh\nexit 0\n' > "$out/bin/storage-sentinel"
              chmod 755 "$out/bin/storage-sentinel"
              echo fixture > "$out/libexec/storage/resource"
            '';
            composed = (mkDarwinConfiguration {
              hostName = defaultHostName;
              extraModules = [ { dinas.extraCommandPackages = [ extra ]; } ];
            }).config.dinas.commandNamespaces.dinas.package;
          in pkgs.runCommandLocal "dinas-composed-command-namespace"
            { nativeBuildInputs = [ (mkTestPython pkgs) pkgs.bash pkgs.coreutils pkgs.gawk ]; }
            ''
              export HOME="$TMPDIR/home"
              export PYTHONDONTWRITEBYTECODE=1
              export DINAS_COMPOSED_COMMAND_PACKAGE=${composed}
              mkdir -p "$HOME"
              python3 -m pytest -q -p no:cacheprovider \
                ${self}/src/test/int/python/composed_command_namespace.int.test.py
              touch "$out"
            '';

        nix-unit-tests = pkgs.runCommandLocal "nix-darwin-nix-unit-tests"
          {
            nativeBuildInputs = [
              pkgs.nix-unit
              pkgs.zsh
            ];
          }
          ''
            ${nixUnitTestRunner} ${./.} ${system} "$out"
          '';

        managed-python-pillow = pkgs.runCommandLocal "managed-python-pillow"
          {
            nativeBuildInputs = [ python ];
          }
          ''
            python3 -c 'from PIL import Image; image = Image.new("RGB", (1, 1)); assert image.size == (1, 1)'
            mkdir -p "$out"
            echo ok > "$out/success"
          '';

        managed-python-pypistats = pkgs.runCommandLocal "managed-python-pypistats"
          {
            nativeBuildInputs = [ python ];
          }
          ''
            python3 -c 'import pypistats'
            python3 -c 'import importlib.metadata; assert importlib.metadata.version("termcolor") == "3.2.0"'
            pypistats --version
            pypistats --help
            mkdir -p "$out"
            echo ok > "$out/success"
          '';

        managed-node-tools = pkgs.runCommandLocal "managed-node-tools"
          {
            nativeBuildInputs = [ node ];
          }
          ''
            case "$(node --version)" in
              v22.*) ;;
              *) echo "managed Node must be major 22" >&2; exit 1 ;;
            esac
            npm --version
            tsc --version
            typescript-language-server --version
            tsx --version
            vite --version
            vitest --version
            mkdir -p "$out"
            echo ok > "$out/success"
          '';

        managed-rust-tools = pkgs.runCommandLocal "managed-rust-tools"
          {
            nativeBuildInputs = [ rust ];
          }
          ''
            rustc --version
            cargo --version
            rustfmt --version
            clippy-driver --version
            rust-analyzer --version
            case "$(cargo-tauri --version)" in
              "tauri-cli 2."*) ;;
              *) echo "managed Tauri CLI must be major 2" >&2; exit 1 ;;
            esac
            mkdir -p "$out"
            echo ok > "$out/success"
          '';

        node-bootstrap-fixture = self.packages.${system}.nodeBootstrapFixture;

        shell-config-projection = pkgs.runCommandLocal "shell-config-projection" { } ''
          grep -Fqx "_dinas_projected_nbin_root=/Users/${userName}/.local/nbin" \
            ${shellConfig}/common/004_dinas-paths
          mkdir -p "$out"
          echo ok > "$out/success"
        '';
      };
  };
}
