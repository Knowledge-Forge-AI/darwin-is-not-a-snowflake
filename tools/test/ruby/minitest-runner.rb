#!/usr/bin/env ruby

require "json"
require "minitest"
require "stringio"

module NixDarwinJsonResults
  @tests = []

  class << self
    def record(result)
      failures = result.failures.map do |failure|
        {
          class: failure.class.name,
          message: failure.message,
          backtrace: failure.backtrace || [],
        }
      end
      location = result.source_location || []
      @tests << {
        name: result.name,
        classname: result.class_name,
        file: location[0],
        line: location[1],
        status: status_for(result),
        time: result.time,
        stdout: result.instance_variable_get(:@nix_darwin_stdout).to_s,
        stderr: result.instance_variable_get(:@nix_darwin_stderr).to_s,
        failures: failures,
      }
    end

    def write
      result_json = ENV["NIX_DARWIN_TEST_RESULT_JSON"]
      return if result_json.nil? || result_json.empty?

      File.write(
        result_json,
        JSON.pretty_generate(
          {
            language: "ruby",
            tests: @tests,
          },
        ),
      )
    end

    private

    def status_for(result)
      return "skipped" if result.skipped?
      return "passed" if result.passed?

      "failed"
    end
  end
end

module NixDarwinMinitestCapture
  def run
    original_stdout = $stdout
    original_stderr = $stderr
    captured_stdout = StringIO.new
    captured_stderr = StringIO.new

    $stdout = captured_stdout
    $stderr = captured_stderr
    result = super
    result.instance_variable_set(:@nix_darwin_stdout, captured_stdout.string)
    result.instance_variable_set(:@nix_darwin_stderr, captured_stderr.string)
    NixDarwinJsonResults.record(result)
    result
  ensure
    $stdout = original_stdout
    $stderr = original_stderr
  end
end

if ENV["NIX_DARWIN_TEST_RESULT_JSON"] && !ENV["NIX_DARWIN_TEST_RESULT_JSON"].empty?
  Minitest::Test.prepend(NixDarwinMinitestCapture)
  Minitest.after_run { NixDarwinJsonResults.write }
end

ARGV.each do |test_file|
  next unless File.file?(test_file)

  load test_file
end
