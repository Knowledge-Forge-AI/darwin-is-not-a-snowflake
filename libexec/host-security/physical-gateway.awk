$1 == "default" &&
  $2 !~ /^link#/ &&
  $NF !~ /^(ipsec|utun|bridge|lo)/ {
    print $2
    exit
  }
