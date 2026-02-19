# Contributing to Julabo Control

Thank you for your interest in contributing to Julabo Control! This document provides guidelines for contributing to the project.

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Julabo-control.git
   cd Julabo-control
   ```
3. Set up your development environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e .[dev]
   ```

## How to Contribute

### Reporting Bugs

- Check if the bug has already been reported in the [Issues](https://github.com/EfrenPy/Julabo-control/issues)
- If not, create a new issue with:
  - A clear, descriptive title
  - Steps to reproduce the problem
  - Expected behavior vs actual behavior
  - Your environment (OS, Python version, Julabo model)
  - Any relevant error messages or logs

### Suggesting Enhancements

- Open an issue with the "enhancement" label
- Clearly describe the feature and its use case
- Explain why this would be useful to other users

### Pull Requests

1. Create a new branch for your feature or bugfix:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes:
   - Write clear, readable code
   - Follow the existing code style
   - Add comments where necessary
   - Update documentation if needed

3. Run the quality checks (all must pass before opening a PR):
   ```bash
   ruff check julabo_control/ tests/        # linting
   mypy julabo_control/                      # type checking
   pytest tests/ --ignore=tests/test_e2e_simulator.py \
       --cov=julabo_control --cov-fail-under=90 -q   # tests + coverage gate
   pytest tests/test_e2e_simulator.py -v     # E2E simulator tests
   ```

4. Test your changes:
   - Ensure the CLI commands still work
   - Test the GUI if you modified it
   - Test remote server/client if applicable
   - Verify compatibility with the Julabo serial protocol

5. Commit your changes:
   ```bash
   git add .
   git commit -m "Brief description of your changes"
   ```

6. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

7. Open a Pull Request on GitHub with:
   - A clear title and description
   - Reference to any related issues
   - Summary of changes made
   - Testing performed

## Pre-PR Checklist

Before submitting a pull request, verify:

- [ ] `ruff check julabo_control/ tests/` passes with no errors
- [ ] `mypy julabo_control/` passes with no errors
- [ ] `pytest` passes with **>90% coverage** (CI enforced)
- [ ] E2E simulator tests pass (`pytest tests/test_e2e_simulator.py -v`)
- [ ] New features include tests
- [ ] CHANGELOG.md updated (if user-facing)

## Code Guidelines

### Python Style

- Follow PEP 8 guidelines
- Use type hints where appropriate
- Keep functions focused and single-purpose
- Use descriptive variable names

### Documentation

- Update README.md if adding new features
- Add docstrings to new functions/classes
- Include inline comments for complex logic

### Serial Communication

- Always test with actual hardware when modifying core communication
- Respect the protocol: 4800 baud, 7-N-1, even parity, RTS/CTS
- Handle errors gracefully with appropriate exceptions
- Verify read-back for all write operations

## Hardware Testing

If you don't have access to Julabo hardware:
- Clearly mark PRs as "untested on hardware"
- Focus on code quality, documentation, and non-hardware features
- Hardware testing will be done by maintainers before merging

## Compatibility

- This project targets Julabo FL1703 and compatible models
- Ensure backward compatibility unless there's a compelling reason
- Document any breaking changes clearly

## Questions?

Feel free to open an issue for any questions about contributing!

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
