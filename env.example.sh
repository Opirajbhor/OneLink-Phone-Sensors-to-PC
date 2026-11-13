# Copy to env.sh and edit the two paths (env.sh is gitignored).
# Android build only needs these; the PC app needs none of them.

# JDK 17 — https://adoptium.net  (unzip, no installer needed)
export JAVA_HOME="/path/to/jdk-17"

# Android SDK with platforms;android-35 and build-tools;35.0.0
export ANDROID_HOME="/path/to/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"

# The build writes android/local.properties with sdk.dir=$ANDROID_HOME
export PATH="$JAVA_HOME/bin:$PATH"
