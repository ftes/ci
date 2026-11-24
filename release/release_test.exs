# The release script's tests: `elixir release/release_test.exs`. The edits run on two changelog
# styles, keep-a-changelog (playwright_ex, phoenix_test_playwright) and Fluffy's themed one.
ExUnit.start()

# A repo's scripts/release_config.exs defines this before the script loads; this one lacks every
# key but `repo`, for `config!/0`.
defmodule ReleaseScript.Config do
  def config, do: %{repo: "ftes/example"}
end

Code.require_file("release.exs", __DIR__)

defmodule ReleaseScript.EditTest do
  use ExUnit.Case, async: true

  alias ReleaseScript.{Edit, Error}

  @driver "Drives [Playwright 1.65.1](https://github.com/microsoft/playwright/releases/tag/v1.65.1), on\nNode 26.13.1."

  # keep-a-changelog: `## [1.2.3] 2026-11-24`, and an empty Unreleased above each release.
  @kac %{
    unreleased: "## [Unreleased]",
    version_prefix: "## [",
    version_suffix: "]",
    date_separator: " ",
    reopen_prereleases: false,
    rc_only: "**RC only:**"
  }

  @kac_changelog """
  # Changelog

  ## Versioning

  Text.

  ## [Unreleased]

  ### Fixed

  - A fix.

  ## [1.65.0-rc.4] 2026-11-23

  #{@driver} No upstream API changes.

  ### Added

  - A feature.

  ## 0.16 and earlier

  ## [0.16.0] 2026-11-04
  """

  # Fluffy's: themed sections above, `## 0.9.0 — 2026-11-24`, and Unreleased reopened after an RC.
  @themed %{
    unreleased: "## Unreleased",
    version_prefix: "## ",
    version_suffix: "",
    date_separator: " — ",
    reopen_prereleases: true,
    rc_only: "**RC only:**"
  }

  @themed_changelog """
  # Changelog for v0.9

  Fluffy 0.9 guards the trapdoor more closely.

  ## Guarding the trapdoor

  Fluffy sleeps when music plays.

  ## Unreleased

  Changes since 0.8.0.

  ### Fixed

  - Fluffy wakes when the harp stops.

  ## 0.8.0 — 2026-11-04

  ### Added

  - A third head.
  """

  @date ~D[2026-11-24]

  # A function, not an attribute: OTP 28 can't store a compiled regex in one.
  defp snippet, do: ~r/\{:playwright, "~> ([^"]+)", hex: :playwright_ex\}/

  describe "release_heading/4" do
    test "renames Unreleased to the version and date" do
      assert Edit.release_heading(@kac_changelog, @kac, "1.65.0-rc.5", @date) ==
               String.replace(@kac_changelog, "## [Unreleased]", "## [1.65.0-rc.5] 2026-11-24")

      assert Edit.release_heading(@themed_changelog, @themed, "0.9.0-rc.6", @date) ==
               String.replace(@themed_changelog, "## Unreleased", "## 0.9.0-rc.6 — 2026-11-24")
    end

    test "fails without an Unreleased heading" do
      assert_raise Error, ~r/no `## \[Unreleased\]` heading/, fn ->
        Edit.release_heading("# Changelog\n", @kac, "1.0.0", @date)
      end
    end
  end

  describe "start_next/3" do
    test "inserts an empty Unreleased above the release" do
      released = Edit.release_heading(@kac_changelog, @kac, "1.65.0-rc.5", @date)

      assert Edit.start_next(released, @kac, "1.65.0-rc.5") ==
               String.replace(
                 @kac_changelog,
                 "## [Unreleased]",
                 "## [Unreleased]\n\n## [1.65.0-rc.5] 2026-11-24"
               )
    end

    test "renames a prerelease's heading back to Unreleased with reopen_prereleases" do
      released = Edit.release_heading(@themed_changelog, @themed, "0.9.0-rc.6", @date)

      assert Edit.start_next(released, @themed, "0.9.0-rc.6") == @themed_changelog
    end

    test "inserts an empty Unreleased above a final release, even with reopen_prereleases" do
      released = Edit.release_heading(@themed_changelog, @themed, "0.9.0", @date)

      assert Edit.start_next(released, @themed, "0.9.0") ==
               String.replace(
                 @themed_changelog,
                 "## Unreleased",
                 "## Unreleased\n\n## 0.9.0 — 2026-11-24"
               )
    end

    test "finds the version's own heading, not a prerelease's" do
      changelog =
        String.replace(@themed_changelog, "## 0.8.0 — 2026-11-04", "## 0.9.0-rc.6 — 2026-11-23")

      released = Edit.release_heading(changelog, @themed, "0.9.0", @date)

      assert Edit.start_next(released, @themed, "0.9.0") =~
               "## Unreleased\n\n## 0.9.0 — 2026-11-24\n\nChanges since 0.8.0."
    end
  end

  describe "snippets and @version" do
    test "bump/3 bumps each snippet and leaves other version requirements alone" do
      readme = """
      {:playwright, "~> 1.65.0-rc.4", hex: :playwright_ex}
      {:playwright, "~> 1.65.0", only: :test}
      Depend on `{:playwright, "~> 1.65.0-rc.4", hex: :playwright_ex}`.
      """

      assert Edit.bump(readme, snippet(), "1.65.0-rc.5") == """
             {:playwright, "~> 1.65.0-rc.5", hex: :playwright_ex}
             {:playwright, "~> 1.65.0", only: :test}
             Depend on `{:playwright, "~> 1.65.0-rc.5", hex: :playwright_ex}`.
             """
    end

    test "requirement/2 takes major.minor.0 for a final version, the full one for a prerelease" do
      assert Edit.requirement("1.65.0", true) == "1.65.0"
      assert Edit.requirement("1.65.3", true) == "1.65.0"
      assert Edit.requirement("1.65.0-rc.5", true) == "1.65.0-rc.5"
      assert Edit.requirement("1.65.3", false) == "1.65.3"
    end

    test "bump/3 fails when the snippet isn't there" do
      assert_raise Error, ~r/no match/, fn -> Edit.bump("nothing", snippet(), "1.0.0") end
    end

    test "bump_snippet/4 names the file whose snippet is gone" do
      assert Edit.bump_snippet(
               "README.md",
               ~s({:playwright, "~> 1.0.0", hex: :playwright_ex}),
               snippet(),
               "1.1.0"
             ) ==
               ~s({:playwright, "~> 1.1.0", hex: :playwright_ex})

      assert_raise Error,
                   ~r/^usage-rules.md: no match for .* \(the release config's snippets\)$/,
                   fn ->
                     Edit.bump_snippet(
                       "usage-rules.md",
                       "Depend on `:playwright`.",
                       snippet(),
                       "1.1.0"
                     )
                   end
    end

    test "reads and sets @version in mix.exs" do
      mix_exs =
        ~s(defmodule P.MixProject do\n  @version "1.65.0-rc.4"\n  @other "1.65.0-rc.4"\nend\n)

      assert Edit.current_version(mix_exs) == "1.65.0-rc.4"

      assert Edit.set_version(mix_exs, "1.65.0") ==
               String.replace(mix_exs, ~s(@version "1.65.0-rc.4"), ~s(@version "1.65.0"))
    end
  end

  describe "driver_line/5" do
    test "inserts the driver, with no upstream API changes when the previous release drove it" do
      released = Edit.release_heading(@kac_changelog, @kac, "1.65.0-rc.5", @date)

      assert Edit.driver_line(released, @kac, "1.65.0-rc.5", "1.65.1", "26.13.1") =~
               "## [1.65.0-rc.5] 2026-11-24\n\n#{@driver} No upstream API changes.\n\n### Fixed\n\n- A fix.\n\n## [1.65.0-rc.4]"
    end

    test "inserts the driver alone when the previous release drove another" do
      released = Edit.release_heading(@kac_changelog, @kac, "1.66.0", @date)

      assert Edit.driver_line(released, @kac, "1.66.0", "1.66.0", "26.14.0") =~
               "## [1.66.0] 2026-11-24\n\n" <>
                 "Drives [Playwright 1.66.0](https://github.com/microsoft/playwright/releases/tag/v1.66.0), on\n" <>
                 "Node 26.14.0.\n\n### Fixed"
    end

    test "keeps a driver line the section has" do
      changelog =
        String.replace(
          @kac_changelog,
          "### Fixed",
          "Drives [Playwright 1.66.0](url).\n\n### Fixed"
        )

      released = Edit.release_heading(changelog, @kac, "1.66.0", @date)

      assert Edit.driver_line(released, @kac, "1.66.0", "1.66.0", "26.14.0") == released
    end
  end

  describe "guards" do
    test "unreleased_content?/2" do
      assert Edit.unreleased_content?(@kac_changelog, @kac)

      refute Edit.unreleased_content?(
               String.replace(@kac_changelog, "### Fixed\n\n- A fix.\n\n", ""),
               @kac
             )

      refute Edit.unreleased_content?(
               "## Unreleased\n\n  \n## 0.8.0 — 2026-11-04\n\n- A third head.\n",
               @themed
             )
    end

    test "newer?/2 compares SemVer, prereleases first" do
      assert Edit.newer?("1.65.0-rc.5", "1.65.0-rc.4")
      assert Edit.newer?("1.65.0-rc.10", "1.65.0-rc.9")
      assert Edit.newer?("1.65.0", "1.65.0-rc.5")
      refute Edit.newer?("1.65.0-rc.4", "1.65.0-rc.4")
      refute Edit.newer?("1.65.0-rc.3", "1.65.0-rc.4")
    end

    test "rc_sections?/3 finds a final version's prerelease sections" do
      refute Edit.rc_sections?(@kac_changelog, @kac, "1.65.0-rc.5")
      assert Edit.rc_sections?(@kac_changelog, @kac, "1.65.0")
      refute Edit.rc_sections?(@kac_changelog, @kac, "1.65.1")

      changelog =
        String.replace(@themed_changelog, "## 0.8.0 — 2026-11-04", "## 0.9.0-rc.6 — 2026-11-23")

      assert Edit.rc_sections?(changelog, @themed, "0.9.0")
      refute Edit.rc_sections?(@themed_changelog, @themed, "0.9.0")
    end

    test "rc_only_entries?/3 finds the marker in a final version's Unreleased only" do
      changelog = String.replace(@kac_changelog, "- A fix.", "- **RC only:** A fix.")
      assert Edit.rc_only_entries?(changelog, @kac, "1.65.0")
      refute Edit.rc_only_entries?(changelog, @kac, "1.65.0-rc.5")
      refute Edit.rc_only_entries?(@kac_changelog, @kac, "1.65.0")

      refute Edit.rc_only_entries?(
               String.replace(@kac_changelog, "- A feature.", "- **RC only:** A feature."),
               @kac,
               "1.65.0"
             )
    end

    test "rc_only_entries?/3 takes the configured marker" do
      changelog = String.replace(@kac_changelog, "- A fix.", "- RC only: A fix.")
      assert Edit.rc_only_entries?(changelog, %{@kac | rc_only: "RC only:"}, "1.65.0")
      refute Edit.rc_only_entries?(changelog, @kac, "1.65.0")
    end

    test "same_minor?/2 compares the driver's major.minor" do
      assert Edit.same_minor?("1.65.0-rc.5", "1.65.1")
      assert Edit.same_minor?("1.65.3", "1.65.0")
      refute Edit.same_minor?("1.65.0", "1.66.0")
      refute Edit.same_minor?("1.65.0", "2.65.0")
      refute Edit.same_minor?("1.65.0", "garbage")
    end

    test "parse_version/1 takes SemVer without build metadata" do
      assert Edit.parse_version("1.65.0-rc.5") == "1.65.0-rc.5"
      assert_raise Error, ~r/not a SemVer version/, fn -> Edit.parse_version("v1.65.0") end
      assert_raise Error, ~r/not a SemVer version/, fn -> Edit.parse_version("1.65") end
      assert_raise Error, ~r/not a SemVer version/, fn -> Edit.parse_version("1.65.0+build.1") end
    end
  end
end

defmodule ReleaseScript.ConfigTest do
  # `check/1` reads the repo's files from the working directory, so this module changes it.
  use ExUnit.Case, async: false

  alias ReleaseScript.Error

  defp config do
    %{
      repo: "ftes/example",
      remote: "origin",
      branch: "main",
      workflow: "ci.yml",
      hex_package: "example",
      snippets: [{"README.md", ~r/\{:example, "~> ([^"]+)"/}],
      driver_line: false,
      driver_minor: false,
      minor_requirement: true,
      changelog: %{
        unreleased: "## [Unreleased]",
        version_prefix: "## [",
        version_suffix: "]",
        date_separator: " ",
        reopen_prereleases: false,
        rc_only: "**RC only:**"
      }
    }
  end

  test "config!/0 validates the repo's config" do
    assert_raise Error, ~r/lacks `remote`, `branch`, .*`changelog.rc_only`$/, fn ->
      ReleaseScript.config!()
    end
  end

  test "validate!/1 names each missing key" do
    # A match, not ==: compiled regexes never compare equal on OTP 28.
    assert %{repo: "ftes/example"} = ReleaseScript.validate!(config())

    c = config() |> Map.delete(:workflow) |> Map.update!(:changelog, &Map.delete(&1, :rc_only))

    assert_raise Error, ~r/lacks `workflow`, `changelog.rc_only`$/, fn ->
      ReleaseScript.validate!(c)
    end
  end

  @tag :tmp_dir
  test "check/1 passes on a matching repo and names what doesn't match", %{tmp_dir: dir} do
    File.write!(
      Path.join(dir, "mix.exs"),
      ~s(defmodule E.MixProject do\n  @version "0.2.0"\nend\n)
    )

    File.write!(Path.join(dir, "README.md"), ~s({:example, "~> 0.2.0"}\n))

    File.write!(
      Path.join(dir, "CHANGELOG.md"),
      "# Changelog\n\n## [0.2.0] 2026-11-24\n\n- A fix.\n"
    )

    File.cd!(dir, fn ->
      assert ExUnit.CaptureIO.capture_io(fn -> ReleaseScript.check(config()) end) =~
               "@version 0.2.0"

      File.write!("CHANGELOG.md", "# Changelog\n\n## 0.2.0 — 2026-11-24\n")

      assert_raise Error, ~r/neither `## \[Unreleased\]` nor `## \[0.2.0\]`/, fn ->
        ReleaseScript.check(config())
      end

      File.write!("README.md", "Depend on it.\n")
      assert_raise Error, ~r/^README.md: no match/, fn -> ReleaseScript.check(config()) end
    end)
  end
end
