NR > 1 {
  covered += $3
  total += $4
}

END {
  printf "%d\t%d\n", covered + 0, total + 0
}
