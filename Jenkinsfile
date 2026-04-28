pipeline {
    agent any

    environment {
        PROJECT_ROOT = "${env.WORKSPACE}"
        VIRTUAL_ENV  = "${env.WORKSPACE}/.ci-venv"
        GIT_LFS_SKIP_SMUDGE = "1"
    }

    options {
        skipDefaultCheckout(true)
        buildDiscarder(logRotator(numToKeepStr: '5'))
    }

    stages {
        stage('Checkout Code') {
            steps {
                echo "📥 Checking out source code..."
                checkout scm
            }
        }

        stage('Setup CI Environment') {
            steps {
                echo "🐍 Creating lightweight CI virtualenv..."
                sh """
                python3 -m venv ${VIRTUAL_ENV}
                . ${VIRTUAL_ENV}/bin/activate
                pip install --quiet --upgrade pip
                pip install --quiet flake8 yamllint
                """
            }
        }

        stage('Python Linting') {
            steps {
                echo "🔍 Linting Python files..."
                sh """
                . ${VIRTUAL_ENV}/bin/activate
                flake8 ${PROJECT_ROOT}/NSOT/python-files/ --max-line-length=150
                """
            }
        }

        stage('YAML Linting') {
            steps {
                echo "📄 Linting YAML files..."
                sh """
                . ${VIRTUAL_ENV}/bin/activate
                find ${PROJECT_ROOT} \
                  -path "*/venv" -prune -o \
                  -path "*/.ci-venv" -prune -o \
                  -path "*/clab-*" -prune -o \
                  \\( -name "*.yml" -o -name "*.yaml" \\) -print \
                  | xargs yamllint -d "{rules: {document-start: disable, truthy: disable}}"
                """
            }
        }

        stage('Run Unit Tests') {
            steps {
                echo "🧪 Running Unit Tests..."
                sh """
                . ${VIRTUAL_ENV}/bin/activate
                pip install --quiet netmiko jinja2 requests
                python3 -m unittest discover -s ${PROJECT_ROOT}/NSOT/python-files -p "test_suite.py"
                """
            }
        }
    }

    post {
        always {
            echo '🧹 Cleaning up workspace'
            deleteDir()
        }
        success {
            echo '✅ Pipeline completed successfully.'
        }
        failure {
            echo '❌ Pipeline failed. Check the console output for details.'
        }
    }
}
