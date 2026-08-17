#!/bin/bash
# Setup Git Hooks for Automated Testing
# Run this script to install/update git hooks

set -e

HOOKS_DIR=".git/hooks"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🔧 Setting up Git Hooks for automated testing..."
echo ""

# Function to create pre-commit hook
create_pre_commit_hook() {
    local hook_file="$PROJECT_ROOT/$HOOKS_DIR/pre-commit"

    cat > "$hook_file" << 'EOF'
#!/bin/bash
# Pre-commit hook: Run security tests before allowing commit

echo "🔒 Running security tests..."

# Run security tests
pytest tests/test_access_control_security.py -v --tb=short

# Check if tests passed
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Security tests failed! Commit blocked."
    echo "Fix the issues before committing."
    echo ""
    exit 1
fi

echo ""
echo "✅ Security tests passed! Proceeding with commit."
exit 0
EOF

    chmod +x "$hook_file"
    echo "✅ Pre-commit hook installed"
}

# Function to create pre-push hook
create_pre_push_hook() {
    local hook_file="$PROJECT_ROOT/$HOOKS_DIR/pre-push"

    cat > "$hook_file" << 'EOF'
#!/bin/bash
# Pre-push hook: Run all tests before allowing push

echo "🧪 Running all tests before push..."

# Run all tests
pytest tests/ -v --tb=short

# Check if tests passed
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Tests failed! Push blocked."
    echo "Fix the issues before pushing."
    echo ""
    exit 1
fi

echo ""
echo "✅ All tests passed! Proceeding with push."
exit 0
EOF

    chmod +x "$hook_file"
    echo "✅ Pre-push hook installed"
}

# Main execution
cd "$PROJECT_ROOT"

echo "Installing hooks in: $HOOKS_DIR"
echo ""

# Create hooks
create_pre_commit_hook
create_pre_push_hook

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Git hooks installed successfully!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Hooks installed:"
echo "  • pre-commit  → Runs security tests before each commit"
echo "  • pre-push    → Runs all tests before each push"
echo ""
echo "To disable temporarily, use:"
echo "  git commit --no-verify"
echo ""
echo "To uninstall hooks:"
echo "  rm .git/hooks/pre-commit .git/hooks/pre-push"
echo ""
