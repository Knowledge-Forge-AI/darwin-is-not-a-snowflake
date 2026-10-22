BEGIN {
  if (field == "") field = "Temperature"
}

index($0, "\"" field "\" =") {
  value = $2 + 0
  if (value > 0) {
    printf "%.1f", value / 100
    found = 1
    exit
  }
}

END {
  if (!found) exit 1
}
