---
name: fake-jira-skill
description: Query Jira for bugs in a project and summarize coverage gaps. Test fixture for e2e external-state field detection.
user-invocable: true
allowed-tools: Read, Write, Bash, mcp__atlassian__searchJiraIssuesUsingJql, mcp__atlassian__getVisibleJiraProjects
---

You analyze historical bug patterns in a Jira project to identify test coverage gaps.

## Inputs

The user provides:
- **project_key**: A Jira project key (e.g., `RHEL`, `MYPROJECT`) identifying which project to query
- **component**: The software component within the project to focus on (e.g., `auth`, `api-gateway`)

## Steps

1. Query Jira using `mcp__atlassian__searchJiraIssuesUsingJql` with `project = {project_key} AND component = {component} AND type = Bug`
2. Read the local repo to find existing test files
3. Cross-reference bugs with test coverage
4. Write a coverage gap report to `output/coverage-report.md`

## Environment

Requires `JIRA_SERVER` environment variable pointing to the target Jira instance.
