/^[[:space:]]*[0-9]+\.[0-9]+\.[0-9]+[[:space:]]*$/ {
  version = $1
}

END {
  print version
}
