# The step summary of a Claude run (claude-code-action's execution file): only the final message
# and each denied call's command, path or URL, as the full transcript can contain secrets and a
# public repo's run summaries are public.
.[] | select(.type == "result")
  | (.result // .subtype),
    (.permission_denials // [] | select(length > 0) | "\n**Denied tool calls (\(length)):**\n",
      (.[] | (.tool_input | .command // .file_path // .path // .pattern // .url // "" | gsub("\\s+"; " ") | .[:200]) as $arg
        | "- \(.tool_name)" + (if $arg == "" then "" else ": `` \($arg) ``" end)))
