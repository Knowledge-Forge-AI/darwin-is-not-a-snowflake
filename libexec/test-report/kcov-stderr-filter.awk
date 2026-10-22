/^kcov: error: / {
  if (pending != "") {
    print pending
  }
  pending = $0
  next
}

pending != "" {
  if ($0 == " is not an integer") {
    pending = ""
    next
  }
  print pending
  pending = ""
}

{
  print
}

END {
  if (pending != "") {
    print pending
  }
}
