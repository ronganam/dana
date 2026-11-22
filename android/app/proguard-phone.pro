# The phone build hasn't been tried with R8 yet: keep it a pass-through.
-dontshrink
-dontoptimize
-dontobfuscate

# UnifiedPush's connector brings Tink (Web Push decryption); Tink's KeysDownloader, which
# the connector never uses, references google-http-client, which is not in the app.
-dontwarn com.google.api.client.http.**
