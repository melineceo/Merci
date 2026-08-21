package xyz.hackerstone.merci.urlforward;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Проверка связи с компьютером.
 *
 * Нужна, чтобы отличать две разные поломки: «Android не позвал наше
 * приложение» и «приложение не достучалось до хоста». Без неё обе выглядят
 * одинаково — ничего не произошло.
 *
 * Это единственная активность с иконкой в меню: перехватчик ссылок сам по
 * себе невидим.
 */
public class CheckActivity extends Activity {

    private static final String PING = "http://192.168.240.1:7749/ping";
    private static final int TIMEOUT_MS = 3000;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        final Handler handler = new Handler(Looper.getMainLooper());

        new Thread(new Runnable() {
            @Override
            public void run() {
                final boolean alive = ping();
                handler.post(new Runnable() {
                    @Override
                    public void run() {
                        Toast.makeText(
                                CheckActivity.this,
                                alive ? "Связь с компьютером есть"
                                        : "Компьютер не отвечает",
                                Toast.LENGTH_LONG).show();
                        finish();
                    }
                });
            }
        }).start();
    }

    private boolean ping() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(PING).openConnection();
            connection.setConnectTimeout(TIMEOUT_MS);
            connection.setReadTimeout(TIMEOUT_MS);
            return connection.getResponseCode() < 400;
        } catch (Exception failed) {
            return false;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
