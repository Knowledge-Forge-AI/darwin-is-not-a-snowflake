{
  line = $0
  sub(/^[[:space:]]+/, "", line)
  sub(/[[:space:]]+$/, "", line)
  if (line == "") {
    next
  }
  if (language == "awk" && line ~ /^#/) {
    next
  }
  if (language == "zsh" && line ~ /^#/) {
    next
  }
  if (language == "applescript" && line ~ /^--/) {
    next
  }
  count++
}

END {
  print count + 0
}
