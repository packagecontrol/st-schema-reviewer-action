# st-schema-reviewer-action

GitHub Action for reviewing package control schema.

## Usage

Add the following to Github Action workflow file.

```yml
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: 3.11

      - name: Run scheme tests
        uses: packagecontrol/st-schema-reviewer-action@v1
        with:
          channel: channel.json
          repository: repository.json
          test_repositories: true
```

| Argument          | required | description
|-------------------|:--------:|------------
| channel           | no       | Relative path to channel.json to verify. Tests are skipped if file does not exist.
| repository        | no       | Relative path to repository.json to verify. Tests are skipped if file does not exist.
| test_repositories | no       | If `true` all remote repositories, listed in given channel are verified.


## Action releases

It's recommended to create releases using semantically versioned tags – for example, v1.1.3 – and keeping major (v1) and minor (v1.1) tags current to the latest appropriate commit.
