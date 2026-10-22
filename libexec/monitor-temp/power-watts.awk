BEGIN {
  split(raw, parts, /[[:space:]]+/)
  value = parts[1] + 0
  unit = tolower(parts[2])

  if (value < 0 || unit == "") exit 1
  if (unit == "mw") value = value / 1000
  else if (unit == "uw") value = value / 1000000
  else if (unit != "w") exit 1

  printf "%.1f", value
}
