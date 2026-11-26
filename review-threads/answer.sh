#!/usr/bin/env bash
# Answers a PR's open review threads (answer/action.yml) from Claude's `threads`: a reply to each,
# and on a fixed one a 👍 on its first comment and the thread resolved. Only threads list.sh wrote to
# tmp/review-threads/<PR>.json, each once. Env: GH_TOKEN, PR, ANSWERS.
# shellcheck disable=SC2016 # $ in single quotes is GraphQL or jq, not shell.
set -euo pipefail

[[ $PR =~ ^[0-9]+$ ]] || { echo "::error::pr: a PR number, got: $PR"; exit 1; }
listed=tmp/review-threads/$PR.json
[[ -f $listed ]] || { echo "::error::No $listed: run ftes/ci/review-threads for PR $PR first."; exit 1; }

# Claude's answer is data: listed threads only, a reply cut to 3000 characters, `fixed` a boolean.
answers=$(jq -c --slurpfile listed "$listed" '
  (env.ANSWERS | try fromjson catch {}) | (.threads // [])
  | map(select(type == "object") | . as $a | ($listed[0][] | select(.id == $a.id)) as $t
      | {id, root: $t.root, fixed: ($a.fixed == true), reply: ($a.reply | tostring | .[:3000])}
      | select(.reply != ""))
  | unique_by(.id)' <<< "{}")

failed=0
while read -r answer; do
  id=$(jq -r .id <<< "$answer")
  body=$(jq -r .reply <<< "$answer")
  # list.sh resolves a thread with this marker on its last reply, should resolving it fail here.
  [[ $(jq -r .fixed <<< "$answer") == true ]] && body+=$'\n\n<!-- review-threads: fixed -->'
  if ! gh api graphql -f thread="$id" -f body="$body" -f query='
      mutation($thread: ID!, $body: String!) {
        addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) { comment { id } }
      }' > /dev/null; then
    echo "::warning::Couldn't reply to thread $id."; failed=1; continue
  fi
  if [[ $(jq -r .fixed <<< "$answer") == true ]]; then
    gh api graphql -f subject="$(jq -r .root <<< "$answer")" -f query='
      mutation($subject: ID!) { addReaction(input: {subjectId: $subject, content: THUMBS_UP}) { reaction { content } } }' > /dev/null ||
      echo "::warning::Couldn't react to thread $id."
    resolved=false
    for attempt in 1 2 3; do
      if gh api graphql -f thread="$id" -f query='
          mutation($thread: ID!) { resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } } }' > /dev/null; then
        resolved=true; break
      fi
      sleep $((attempt * 5))
    done
    if [[ $resolved == true ]]; then
      echo "Thread $id: fixed, resolved."
    else
      echo "::warning::Couldn't resolve thread $id; the next ftes/ci/review-threads run does."; failed=1
    fi
  else
    echo "Thread $id: declined, left open."
  fi
done < <(jq -c '.[]' <<< "$answers")
unanswered=$(jq --argjson answers "$answers" '[.[] | select(.id as $id | $answers | all(.id != $id))] | length' "$listed")
[[ $unanswered -eq 0 ]] || echo "::warning::Claude left $unanswered open review threads unanswered."
exit "$failed"
