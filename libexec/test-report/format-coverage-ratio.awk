BEGIN {
  if (total == 0) {
    printf "0 / 0 (0%%)"
  } else {
    printf "%d / %d (%g%%)", covered, total, covered * 100 / total
  }
}
