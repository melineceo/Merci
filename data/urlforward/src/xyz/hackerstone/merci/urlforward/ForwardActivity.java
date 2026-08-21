package xyz.hackerstone.merci.urlforward;

import android.app.Activity;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.widget.Toast;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/**
 * Принимает ссылку и отдаёт её хосту.
 *
 * Отправка идёт на шлюз контейнера (192.168.240.1) — это и есть хост со
 * стороны Waydroid. Служба Merci слушает там только этот адрес, поэтому
 * снаружи машины порт недоступен.
 *
 * Активность без интерфейса: тема NoDisplay, сеть в отдельном потоке,
 * сразу finish() — пользователь видит только открывшийся браузер.
 */
public class ForwardActivity extends Activity {

    private static final String TAG = "MerciUrlForward";
    private static final String HOST = "http://192.168.240.1:7749/open";
    private static final int TIMEOUT_MS = 2000;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        Uri data = getIntent() != null ? getIntent().getData() : null;
        if (data == null) {
            finish();
            return;
        }

        final String url = data.toString();
        new Thread(new Runnable() {
            @Override
            public void run() {
                send(url);
            }
        }).start();

        Toast.makeText(this, "Открываю на компьютере", Toast.LENGTH_SHORT).show();
        finish();
    }

    private void send(String url) {
        HttpURLConnection connection = null;
        try {
            URL endpoint = new URL(HOST + "?url=" + URLEncoder.encode(url, "UTF-8"));
            connection = (HttpURLConnection) endpoint.openConnection();
            connection.setConnectTimeout(TIMEOUT_MS);
            connection.setReadTimeout(TIMEOUT_MS);
            connection.setRequestMethod("GET");
            Log.i(TAG, "ответ хоста: " + connection.getResponseCode());
        } catch (Exception failure) {
            // Ронять приложение незачем, а вот молчать — вредно: без записи
            // в журнале «ссылка не открылась» выглядит как пустота, и найти
            // причину нечем.
            Log.w(TAG, "не отдали ссылку хосту: " + failure);
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
