# Core-owned development Chrome policy. Independent of Browser UX/family defaults.
# The JSON contract is also shipped in the attended qualification kit.
(builtins.fromJSON (builtins.readFile ./core-ownership.json)).chrome.policies
