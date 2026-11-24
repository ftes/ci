# The smallest project `setup` runs on, for check.yml's setup job.
defmodule Fixture.MixProject do
  use Mix.Project

  def project, do: [app: :fixture, version: "0.1.0", deps: []]
end
