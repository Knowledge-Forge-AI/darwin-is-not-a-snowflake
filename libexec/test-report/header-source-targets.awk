NR == 1 && $0 ~ /^#!/ {
  next
}

$0 ~ /^[[:space:]]*$/ {
  next
}

$0 ~ /^[[:space:]]*#/ {
  line = $0
  if (sub(/^[[:space:]]*# nix-darwin-test-source-target:[[:space:]]*/, "", line)) {
    print line
  }
  next
}

{
  exit
}
