# nix-darwin-test-source-target: lib/application-claims.nix
{ repoRoot, system }:

let
  flake = builtins.getFlake "path:${toString repoRoot}";
  pkgs = flake.legacyPackages.${system};
  lib = pkgs.lib;
  claims = import (repoRoot + "/lib/application-claims.nix") { inherit lib; };

  attach = owners:
    lib.concatLists (lib.mapAttrsToList claims.attachOwner owners);

  accepts = owners: (claims.validate (attach owners)).ok;

  rejectsWith = rule: owners:
    let result = claims.validate (attach owners); in
    !result.ok && builtins.any (error: lib.hasInfix "(${rule})" error) result.errors;

  vlc = {
    id = "bundle:org.videolan.vlc";
    provider = "homebrew-cask";
    package = "vlc";
    artifact = "VLC.app";
  };

  discord = {
    id = "bundle:com.hnc.Discord";
    provider = "homebrew-cask";
    package = "discord";
    artifact = "Discord.app";
  };

  handoff = {
    from = "core";
    adr = "docs/adr/2026/08/0003-entertainment-nd-migration.md";
  };

  destination = discord // { inherit handoff; };
in
{
  testValidSingletonClaimIsAccepted = {
    expr = accepts { core = [ vlc ]; };
    expected = true;
  };

  testDuplicateBraveCaskRejectedAndCoreSuiteAccepted = {
    expr = let
      brave = { id = "bundle:com.brave.Browser"; provider = "nix"; package = "brave"; artifact = "Brave Browser.app"; };
      youtube = brave // { id = "bundle:com.operator.brave-youtube"; artifact = "Brave YouTube.app"; };
    in {
      duplicate = rejectsWith "duplicate-claim" {
        core = [ brave ];
        browser-ux = [ (brave // { provider = "homebrew-cask"; package = "brave-browser"; }) ];
      };
      sharedPackage = accepts { core = [ (brave // { suite = true; }) (youtube // { suite = true; }) ]; };
    };
    expected = { duplicate = true; sharedPackage = true; };
  };

  testMasIdentityFallbackIsAcceptedForABundleUnknownStoreApplication = {
    expr = accepts {
      core = [{
        id = "mas:1611378436";
        provider = "mas";
        package = "1611378436";
        artifact = "Pure Paste.app";
      }];
    };
    expected = true;
  };

  testMalformedIdentitiesAreRejected = {
    expr = {
      empty = rejectsWith "unsupported-identity-family" {
        core = [ (vlc // { id = ""; }) ];
      };
      displayName = rejectsWith "unsupported-identity-family" {
        core = [ (vlc // { id = "VLC"; }) ];
      };
      caskToken = rejectsWith "unsupported-identity-family" {
        core = [ (vlc // { id = "cask:vlc"; }) ];
      };
      emptyBundle = rejectsWith "malformed-bundle-id" {
        core = [ (vlc // { id = "bundle:"; }) ];
      };
      singleSegmentBundle = rejectsWith "malformed-bundle-id" {
        core = [ (vlc // { id = "bundle:Firefox"; }) ];
      };
      emptyBundleSegment = rejectsWith "malformed-bundle-id" {
        core = [ (vlc // { id = "bundle:org..vlc"; }) ];
      };
      nonDecimalMas = rejectsWith "malformed-mas-id" {
        core = [ (vlc // { id = "mas:16a1"; }) ];
      };
      emptyMas = rejectsWith "malformed-mas-id" {
        core = [ (vlc // { id = "mas:"; }) ];
      };
      zeroMas = rejectsWith "non-positive-mas-id" {
        core = [ (vlc // { id = "mas:0"; }) ];
      };
    };
    expected = {
      empty = true;
      displayName = true;
      caskToken = true;
      emptyBundle = true;
      singleSegmentBundle = true;
      emptyBundleSegment = true;
      nonDecimalMas = true;
      emptyMas = true;
      zeroMas = true;
    };
  };

  testProviderPackageAndArtifactFieldsAreConstrained = {
    expr = {
      unsupportedProvider = rejectsWith "unsupported-provider" {
        core = [ (vlc // { provider = "applite"; }) ];
      };
      emptyPackage = rejectsWith "empty-package" {
        core = [ (vlc // { package = ""; }) ];
      };
      nonDecimalMasPackage = rejectsWith "malformed-mas-package" {
        core = [ (vlc // { id = "mas:12"; provider = "mas"; package = "pure-paste"; }) ];
      };
      emptyArtifact = rejectsWith "malformed-artifact" {
        core = [ (vlc // { artifact = ""; }) ];
      };
      artifactWithoutSuffix = rejectsWith "malformed-artifact" {
        core = [ (vlc // { artifact = "VLC"; }) ];
      };
      operatorPathArtifact = rejectsWith "malformed-artifact" {
        core = [ (vlc // { artifact = "/Users/operator/Applications/VLC.app"; }) ];
      };
      nonBooleanSuite = rejectsWith "suite-not-a-boolean" {
        core = [ (vlc // { suite = "yes"; }) ];
      };
    };
    expected = {
      unsupportedProvider = true;
      emptyPackage = true;
      nonDecimalMasPackage = true;
      emptyArtifact = true;
      artifactWithoutSuffix = true;
      operatorPathArtifact = true;
      nonBooleanSuite = true;
    };
  };

  testModuleSuppliedOwnerAndUnknownKeysAreRejected = {
    expr = {
      owner = rejectsWith "unknown-keys" {
        core = [ (vlc // { owner = "core"; }) ];
      };
      priority = rejectsWith "unknown-keys" {
        core = [ (vlc // { priority = 1; }) ];
      };
      missing = rejectsWith "missing-keys" {
        core = [ (builtins.removeAttrs vlc [ "artifact" ]) ];
      };
      wrongType = rejectsWith "id-not-a-string" {
        core = [ (vlc // { id = 42; }) ];
      };
    };
    expected = {
      owner = true;
      priority = true;
      missing = true;
      wrongType = true;
    };
  };

  testBundleIdentityCollisionComparisonIsCaseNormalized = {
    expr = rejectsWith "duplicate-claim" {
      core = [ discord ];
      entertainment-nd = [ (discord // { id = "bundle:COM.Hnc.discord"; }) ];
    };
    expected = true;
  };

  testArtifactCollisionComparisonIsCaseNormalized = {
    expr = rejectsWith "artifact-collision" {
      core = [ (vlc // { artifact = "VLC.app"; }) ];
      entertainment-nd = [{
        id = "bundle:org.videolan.vlc-nightly";
        provider = "nix";
        package = "vlc";
        artifact = "vlc.APP";
      }];
    };
    expected = true;
  };

  testDifferentIdentitiesTargetingOneBundleAreFatal = {
    expr = rejectsWith "artifact-collision" {
      core = [{
        id = "bundle:com.example.foo";
        provider = "homebrew-cask";
        package = "foo";
        artifact = "Foo.app";
      }];
      developer-ux-nd = [{
        id = "bundle:com.example.foo-fork";
        provider = "nix";
        package = "foo-fork";
        artifact = "Foo.app";
      }];
    };
    expected = true;
  };

  testOneProviderPackageAssertingTwoIdentitiesIsFatalUnlessDeclaredASuite = {
    expr = {
      drift = rejectsWith "provider-package-drift" {
        core = [
          discord
          (discord // { id = "bundle:com.hnc.DiscordPTB"; artifact = "Discord PTB.app"; })
        ];
      };
      declaredSuite = accepts {
        core = [
          (discord // { suite = true; })
          (discord // {
            id = "bundle:com.hnc.DiscordPTB";
            artifact = "Discord PTB.app";
            suite = true;
          })
        ];
      };
      partiallyDeclaredSuite = rejectsWith "provider-package-drift" {
        core = [
          (discord // { suite = true; })
          (discord // { id = "bundle:com.hnc.DiscordPTB"; artifact = "Discord PTB.app"; })
        ];
      };
      staleMarker = rejectsWith "stale-suite-marker" {
        core = [ (discord // { suite = true; }) ];
      };
    };
    expected = {
      drift = true;
      declaredSuite = true;
      partiallyDeclaredSuite = true;
      staleMarker = true;
    };
  };

  testOrdinaryDuplicateClaimsAreFatal = {
    expr = rejectsWith "duplicate-claim" {
      core = [ discord ];
      entertainment-nd = [ discord ];
    };
    expected = true;
  };

  testDirectionalTwoClaimHandoffIsAccepted = {
    expr = accepts {
      core = [ discord vlc ];
      entertainment-nd = [ destination ];
    };
    expected = true;
  };

  testHandoffFailsClosedInEveryDirectionItWasNotDesignedFor = {
    expr = {
      sourceMissing = rejectsWith "singleton-handoff" {
        entertainment-nd = [ destination ];
      };
      sourceWrong = rejectsWith "handoff-source-mismatch" {
        desktop-ux-nd = [ discord ];
        entertainment-nd = [ destination ];
      };
      unknownSource = rejectsWith "handoff-source-mismatch" {
        core = [ discord ];
        entertainment-nd = [ (discord // { handoff = handoff // { from = "office-nd"; }; }) ];
      };
      mutual = rejectsWith "mutual-handoff" {
        core = [ (discord // { handoff = handoff // { from = "entertainment-nd"; }; }) ];
        entertainment-nd = [ destination ];
      };
      threeClaims = rejectsWith "too-many-claims" {
        core = [ discord ];
        desktop-ux-nd = [ discord ];
        entertainment-nd = [ destination ];
      };
      sameOwner = rejectsWith "handoff-same-owner" {
        core = [ discord destination ];
      };
      artifactMismatch = rejectsWith "handoff-artifact-mismatch" {
        core = [ discord ];
        entertainment-nd = [ (destination // { artifact = "Discord Canary.app"; }) ];
      };
      emptyAdr = rejectsWith "handoff-adr-empty" {
        core = [ discord ];
        entertainment-nd = [ (discord // { handoff = handoff // { adr = ""; }; }) ];
      };
      emptyFrom = rejectsWith "handoff-from-empty" {
        core = [ discord ];
        entertainment-nd = [ (discord // { handoff = handoff // { from = ""; }; }) ];
      };
      wildcardFrom = rejectsWith "handoff-from-wildcard" {
        core = [ discord ];
        entertainment-nd = [ (discord // { handoff = handoff // { from = "*"; }; }) ];
      };
      operatorPathAdr = rejectsWith "handoff-adr-not-tracked" {
        core = [ discord ];
        entertainment-nd = [ (discord // { handoff = handoff // { adr = "/Users/operator/adr.md"; }; }) ];
      };
      extraHandoffKey = rejectsWith "handoff-keys" {
        core = [ discord ];
        entertainment-nd = [ (discord // { handoff = handoff // { until = "2026-09-01"; }; }) ];
      };
      missingHandoffKey = rejectsWith "handoff-keys" {
        core = [ discord ];
        entertainment-nd = [ (discord // { handoff = { from = "core"; }; }) ];
      };
    };
    expected = {
      sourceMissing = true;
      sourceWrong = true;
      unknownSource = true;
      mutual = true;
      threeClaims = true;
      sameOwner = true;
      artifactMismatch = true;
      emptyAdr = true;
      emptyFrom = true;
      wildcardFrom = true;
      operatorPathAdr = true;
      extraHandoffKey = true;
      missingHandoffKey = true;
    };
  };

  testAHandoffRemovesItselfWhenTheSourceFootprintGoes = {
    expr = {
      duringParity = accepts {
        core = [ discord ];
        entertainment-nd = [ destination ];
      };
      afterSourceRemoval = rejectsWith "singleton-handoff" {
        entertainment-nd = [ destination ];
      };
      afterHandoffRemoval = accepts {
        entertainment-nd = [ discord ];
      };
    };
    expected = {
      duringParity = true;
      afterSourceRemoval = true;
      afterHandoffRemoval = true;
    };
  };

  testFailureMessagesCarryEnoughContextToActOn = {
    expr =
      let
        errors = (claims.validate (attach {
          core = [ discord ];
          entertainment-nd = [ (discord // { provider = "nix"; package = "discord-unstable"; }) ];
        })).errors;
        message = builtins.head errors;
        carries = token: lib.hasInfix token message;
      in
      {
        singleError = builtins.length errors == 1;
        normalizedId = carries "id: bundle:com.hnc.discord";
        declaredId = carries "id=bundle:com.hnc.Discord";
        bothOwners = carries "core" && carries "entertainment-nd";
        bothProviders = carries "provider=homebrew-cask" && carries "provider=nix";
        bothPackages = carries "package=discord" && carries "package=discord-unstable";
        artifact = carries "artifact=Discord.app";
        rule = carries "(duplicate-claim)";
        remedy = carries "handoff = {";
      };
    expected = {
      singleError = true;
      normalizedId = true;
      declaredId = true;
      bothOwners = true;
      bothProviders = true;
      bothPackages = true;
      artifact = true;
      rule = true;
      remedy = true;
    };
  };

  testOwnerIdentityIsAttachedByTheCompositionRatherThanSelfReported = {
    expr = {
      attached = claims.attachOwner "core" [ vlc ];
      emptyOwnerThrows = !(builtins.tryEval (claims.attachOwner "" [ vlc ])).success;
      wildcardOwnerThrows = !(builtins.tryEval (claims.attachOwner "*" [ vlc ])).success;
    };
    expected = {
      attached = [{ owner = "core"; claim = vlc; }];
      emptyOwnerThrows = true;
      wildcardOwnerThrows = true;
    };
  };

  testAssertValidThrowsOnCollisionAndPassesTheValueThroughOtherwise = {
    expr = {
      passes = claims.assertValid (attach { core = [ vlc ]; }) "ok";
      throws = !(builtins.tryEval
        (claims.assertValid (attach { core = [ vlc vlc ]; }) "ok")).success;
    };
    expected = {
      passes = "ok";
      throws = true;
    };
  };

  testProviderSetIsClosedToTheThreeAcceptedDeploymentSystems = {
    expr = lib.sort (a: b: a < b) claims.providers;
    expected = [ "homebrew-cask" "mas" "nix" ];
  };

  testNormalizationLowercasesIdentityAndArtifactComparisonKeys = {
    expr = {
      id = claims.normalizeId "bundle:COM.Example.App";
      artifact = claims.normalizeArtifact "Foo.APP";
      preservesDeclaredSpelling =
        (claims.normalize { owner = "core"; claim = discord; }).id;
    };
    expected = {
      id = "bundle:com.example.app";
      artifact = "foo.app";
      preservesDeclaredSpelling = "bundle:com.hnc.Discord";
    };
  };
}
