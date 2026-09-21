# Milestone 6: configurable small Gradle/JUnit tasks

This adds `submit-gradle`: choose a local Git repository, a committed base, a task prompt, and an approved production Java file. The model receives the task and that file's full source (up to 900 bytes), replaces the selected file, and gets at most one correction attempt. Each candidate is built with Gradle and verified using JUnit XML. Successful changes must also pass the existing targeted text review before being committed locally. Gradle results can be previewed and published using the existing explicit publisher once the destination repository contains the exact base commit.

This is a constrained coding workflow, not a general autonomous repository explorer. One file is edited per job; tests and build files are preserved. The model does not yet select files, browse dependencies, add tests, edit multiple files, or plan large features. Up to eight selectable files can be listed in committed `.nullcode.json`. Task length is 500 bytes; input source length is 900 bytes to fit the existing controller's 2000-byte input budget. Output is limited to 4096 bytes plus the controller's existing token cap. The profile permits Java package declarations and arbitrary public type names.

## Build and testing

The approved build uses Java 21, Gradle 8.14.3, JUnit Jupiter 5.11.4 and launcher 1.11.4. This profile uses a versioned Gradle container rather than a project wrapper. `build.gradle`, `settings.gradle`, and `gradle.properties` must match the supplied templates. Custom plugins, dependencies and build commands are not supported yet. A dependency-cache image is built once with internet access and runs an offline JUnit smoke test during its build.

Job containers have networking disabled, a read-only source snapshot, no credentials or Docker socket, a temporary writable build/cache directory, 1.5 GiB memory and two CPU cores. Compile/test operations each have a four-minute timeout. XML reports must contain at least the configured number of non-skipped cases and zero failures; missing, invalid, skipped-only, or insufficient results cannot pass. The runner saves XML, logs, source hashes, diffs and review results. Test/build code still executes in a container sharing the Pi kernel, not a VM.

The example Java lab has Slugs.slugify to implement and a working TextStats.countWords class. Twelve JUnit tests cover both. This checks a new feature while preserving existing behavior. Tests are prewritten acceptance checks; this milestone does not claim the model generated them.

## Install

Let active jobs finish before updating the worker. On the Pi:

```sh
sudo systemctl stop nullcode-worker
tar -xzf ~/nullcode-milestone-6.tar.gz -C /srv/nullbrain/src
cd /srv/nullbrain/src/nullcode
python3 -m unittest -v test_workflow.py test_repo_workflow.py test_review.py test_publish.py test_gradle.py
sudo docker build -t nullcode-gradle:8.14.3-jdk21 gradle_profile
sudo systemctl start nullcode-worker
python3 create_gradle_fixture.py /srv/nullbrain/repos/nullcode-java-lab
```

Expected Python test count: 50. The local development environment can test Python and Git but cannot run Docker. The Pi image build and first job establish actual Gradle/JUnit/model behavior. No Rust rebuild or database migration is required. The fixture creator refuses to overwrite existing repositories. New source files are delivered alongside the old profiles; the Numbers jobs and publisher remain supported.

## Submit a useful task

```sh
python3 java_workflow.py submit-gradle \
  --repo /srv/nullbrain/repos/nullcode-java-lab --base main \
  --file src/main/java/lab/Slugs.java \
  --task 'Implement slugify: reject null with IllegalArgumentException; lowercase using Locale.ROOT; replace each run of characters outside a-z and 0-9 with one hyphen; strip leading and trailing hyphens. Empty or punctuation-only input returns an empty string. Preserve the package and API.'
python3 java_workflow.py wait 5
```

Use the returned workflow ID. Expected: Gradle compilation, all 12 JUnit cases passing, a passing targeted review, and a local task commit changing only Slugs.java. Failure remains recorded if the model cannot complete it within the attempt budget.

The repository selection, file, and task are real inputs. To use another tiny project, start from the approved build files, list its selectable Java files in `.nullcode.json`, set a meaningful minimum test count, write acceptance tests, and commit that baseline before submission.

## Publishing

The Numbers sandbox has unrelated history. Do not publish this project to it. When ready, use a separate GitHub repository containing the Java lab baseline; the publisher requires its main SHA to match the recorded base. Publishing remains explicit with `publish_workflow.py ID --repo owner/repository --publish`. No new remote repository or PR is created by installing this milestone.

Gradle references: https://docs.gradle.org/current/userguide/command_line_interface.html and https://docs.gradle.org/current/userguide/java_testing.html
