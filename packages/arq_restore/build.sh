runHook preBuild

mkdir -p build/obj

include_flags=()
while IFS= read -r include_dir; do
  include_flags+=("-I$include_dir")
done < <(find . -type d | sort)

common_flags=(
  -mmacosx-version-min=13.0
  -DUSE_OPENSSL=1
  -include arq_restore_Prefix.pch
  "${include_flags[@]}"
)

no_arc_sources=(
  "NSObject+SBJSON.m"
  "NSString+SBJSON.m"
  "SBJsonBase.m"
  "SBJsonParser.m"
  "SBJsonWriter.m"
)

uses_arc() {
  local source_basename="$1"
  local no_arc_source

  for no_arc_source in "${no_arc_sources[@]}"; do
    if [ "$source_basename" = "$no_arc_source" ]; then
      return 1
    fi
  done

  return 0
}

objects=()
while IFS= read -r source_path; do
  object_path="build/obj/${source_path#./}.o"
  mkdir -p "$(dirname "$object_path")"

  source_flags=()
  case "$source_path" in
    *.m)
      if uses_arc "$(basename "$source_path")"; then
        source_flags=(-fobjc-arc)
      else
        source_flags=(-fno-objc-arc)
      fi
      ;;
  esac

  "$CC" -c "${common_flags[@]}" "${source_flags[@]}" \
    "$source_path" -o "$object_path"
  objects+=("$object_path")
done < <(find . \( -name '*.m' -o -name 'sqlite3.c' -o -name 'lz4.c' \) | sort)

"$CC" -mmacosx-version-min=13.0 \
  "${objects[@]}" \
  3rdparty/openssl-1.1.1h/lib/libcrypto.a \
  3rdparty/openssl-1.1.1h/lib/libssl.a \
  -framework Foundation \
  -framework SystemConfiguration \
  -framework CoreFoundation \
  -framework Security \
  -framework CoreServices \
  -framework IOKit \
  -framework AppKit \
  -lz \
  -lxml2 \
  -o build/arq_restore

runHook postBuild
