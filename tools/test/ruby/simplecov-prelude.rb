require "simplecov"

SimpleCov.coverage_dir(ENV.fetch("NIX_DARWIN_TEST_COVERAGE_DIR"))
SimpleCov.root(ENV.fetch("NIX_DARWIN_TEST_ROOT"))
SimpleCov.enable_coverage :branch
SimpleCov.start do
  add_filter "/src/test/"
  add_filter "/tools/test/"
end
