#!/usr/bin/env elixir
# Releases one of ftes's Hex libraries:
#
#     release/release.exs <repo> [--dry-run] <version>    e.g. release/release.exs fluffy 0.9.0
#
# Works in a fresh clone in a temporary directory (`gh repo clone`, so your protocol and
# credentials), never in your checkouts. Commits "Release <version>" and "Start the next
# release" with jj, prints them, then pushes main and tags the first; the tag runs the repo's CI,
# whose publish job releases to Hex. --dry-run stops before the push. Any problem raises.
#
# Conventions instead of per-repo config:
#   - mix.exs has `@version "…"` and `app: :name`.
#   - Install lines `{:name, "~> <current requirement>"` in Markdown files are bumped; at least
#     one must exist. CHANGELOG.md and upgrade guides (upgrading*.md) are left alone. A final
#     version is required as `~> major.minor.0`, a prerelease as itself.
#   - CHANGELOG.md has `## [Unreleased]` or `## Unreleased`; a release heading copies the latest
#     one's date separator (`## [1.2.3] 2026-11-24`, `## 1.2.3 — 2026-11-24`). Unreleased gathers
#     everything since the last final release: a prerelease's heading goes back to Unreleased
#     after it, a final release gets an empty Unreleased above it. A final release refuses
#     `RC only:` entries and leftover `<version>-…` sections.
#   - With DRIVER_VERSION (playwright_ex), the version's major.minor must be the driver's, and
#     Unreleased's `Drives [Playwright …]` line is set from DRIVER_VERSION and NODE_VERSION.

defmodule Release do
  def main(argv) do
    {repo, version, dry_run?} =
      case OptionParser.parse(argv, strict: [dry_run: :boolean]) do
        {opts, [repo, version], []} -> {repo, version, opts[:dry_run] == true}
        _ -> raise "usage: release/release.exs <repo> [--dry-run] <version>"
      end

    repo = if repo =~ "/", do: repo, else: "ftes/" <> repo
    v = Version.parse!(version)
    if v.build, do: raise("Hex refuses build metadata: #{version}")
    final? = v.pre == []
    tag = "v#{version}"

    tags = sh!("gh", ["api", "repos/#{repo}/git/matching-refs/tags/#{tag}", "--jq", ".[].ref"])
    if "refs/tags/#{tag}" in String.split(tags), do: raise("#{tag} exists on GitHub")

    dir = Path.join(System.tmp_dir!(), "release-#{Path.basename(repo)}-#{System.os_time()}")
    sh!("gh", ["repo", "clone", repo, dir, "--", "--quiet"])
    File.cd!(dir)
    sh!("jj", ["git", "init", "--colocate"])

    # mix.exs
    mix_exs = File.read!("mix.exs")

    [_, current] =
      Regex.run(~r/^  @version "([^"]+)"$/m, mix_exs) || raise("no @version in mix.exs")

    [_, app] = Regex.run(~r/\bapp: :(\w+)/, mix_exs) || raise("no `app: :name` in mix.exs")

    if Version.compare(version, current) != :gt,
      do: raise("#{version} isn't greater than @version #{current}")

    File.write!(
      "mix.exs",
      String.replace(mix_exs, ~s(@version "#{current}"), ~s(@version "#{version}"))
    )

    # Install lines
    install = ~r/(\{:#{app},\s*"~> )#{Regex.escape(requirement(current))}"/

    bumped =
      for path <- Path.wildcard("**/*.md"),
          path != "CHANGELOG.md" and not String.starts_with?(Path.basename(path), "upgrading"),
          (content = File.read!(path)) =~ install do
        # A function, not "\\1…": a version's leading digit would extend the group number.
        bump = fn _, head -> head <> requirement(version) <> ~s(") end
        File.write!(path, Regex.replace(install, content, bump))
      end

    if bumped == [],
      do: raise(~s(no `{:#{app}, "~> #{requirement(current)}"` install line in *.md))

    # CHANGELOG.md
    changelog = File.read!("CHANGELOG.md")
    brackets? = changelog =~ ~r/^## \[Unreleased\]$/m
    unreleased = if brackets?, do: "## [Unreleased]", else: "## Unreleased"
    heading = if brackets?, do: "## [#{version}]", else: "## #{version}"

    separator =
      case Regex.run(~r/^## \[?\d+\.\d+\.\d+[^\s\]]*\]?( — | )\d{4}-\d\d-\d\d$/m, changelog) do
        [_, separator] -> separator
        nil -> " "
      end

    section = ~r/^#{Regex.escape(unreleased)}\n(.*?)(?=^## |\z)/ms

    [{start, length}, {body_start, body_length}] =
      Regex.run(section, changelog, return: :index) || raise("no `#{unreleased}` in CHANGELOG.md")

    body = binary_part(changelog, body_start, body_length)
    below = binary_part(changelog, start + length, byte_size(changelog) - start - length)
    if String.trim(body) == "", do: raise("`#{unreleased}` is empty")

    if final? and (body =~ "RC only:" or changelog =~ ~r/^## \[?#{Regex.escape(version)}-/m),
      do: raise("a final release: merge the RC sections into Unreleased, drop `RC only:` entries")

    body =
      if File.exists?("DRIVER_VERSION") do
        driver = String.trim(File.read!("DRIVER_VERSION"))
        node = String.trim(File.read!("NODE_VERSION"))
        d = Version.parse!(driver)

        if {d.major, d.minor} != {v.major, v.minor},
          do: raise("DRIVER_VERSION #{driver} isn't #{version}'s major.minor")

        previous = Regex.run(~r/Drives \[Playwright ([^\]]+)\]/, below, capture: :all_but_first)
        same = if previous == [driver], do: " No upstream API changes.", else: ""
        url = "https://github.com/microsoft/playwright/releases/tag/v#{driver}"
        line = "Drives [Playwright #{driver}](#{url}), on\nNode #{node}.#{same}"

        if body =~ "Drives [Playwright ",
          do: Regex.replace(~r/Drives \[Playwright .*?(?=\n\n)/s, body, line, global: false),
          else: "\n" <> line <> "\n" <> body
      else
        body
      end

    {date, _} = :calendar.local_time()
    release_heading = "#{heading}#{separator}#{Date.to_iso8601(Date.from_erl!(date))}"
    above = binary_part(changelog, 0, start)
    released = above <> release_heading <> "\n" <> body <> below

    # A prerelease's entries stay in Unreleased; a final release starts an empty one.
    next =
      if final?,
        do: above <> unreleased <> "\n\n" <> release_heading <> "\n" <> body <> below,
        else: above <> unreleased <> "\n" <> body <> below

    File.write!("CHANGELOG.md", released)
    sh!("jj", ["commit", "-m", "Release #{version}"])
    File.write!("CHANGELOG.md", next)
    sh!("jj", ["commit", "-m", "Start the next release"])
    IO.puts(sh!("jj", ["log", "--git", "--no-graph", "-r", "main@origin..@-"]))

    if dry_run? do
      IO.puts("\nDry run: nothing pushed. The commits are in #{dir}.")
    else
      sh!("jj", ["bookmark", "track", "main", "--remote", "origin"])
      sh!("jj", ["bookmark", "set", "main", "-r", "@-"])
      sh!("jj", ["git", "push", "-b", "main"])
      # Read after the push: signing on push rewrites the commits.
      sha = sh!("jj", ["log", "--no-graph", "-r", "main@origin-", "-T", "commit_id"])

      sh!("gh", [
        "api",
        "repos/#{repo}/git/refs",
        "-f",
        "ref=refs/tags/#{tag}",
        "-f",
        "sha=#{sha}"
      ])

      IO.puts("""

      Released #{tag} (#{sha}). CI publishes it: https://github.com/#{repo}/actions
      Approve the staged release on hex.pm when its publish job waits.\
      """)
    end
  end

  # `~> major.minor.0` takes a final version's patches; a prerelease is required as itself.
  defp requirement(version) do
    case Version.parse!(version) do
      %{pre: []} = v -> "#{v.major}.#{v.minor}.0"
      _ -> version
    end
  end

  # Raises on a non-zero exit; stderr goes to the terminal. `env -u DEVELOPER_DIR`: a leaked one
  # keeps jj and gh from finding git's tools. `--color=never`: ids are read back; `--quiet`: no hints.
  defp sh!(tool, args) do
    args = if tool == "jj", do: ["--color=never", "--quiet" | args], else: args

    case System.cmd("env", ["-u", "DEVELOPER_DIR", tool | args]) do
      {out, 0} -> String.trim(out)
      {out, status} -> raise "`#{Enum.join([tool | args], " ")}` exited with #{status}\n#{out}"
    end
  end
end

Release.main(System.argv())
