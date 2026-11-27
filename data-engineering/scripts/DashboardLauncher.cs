using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("SwarmLink Dashboard")]
[assembly: AssemblyDescription("Start or reopen the local SwarmLink live dashboard")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class DashboardLauncher
{
    private static string FindPython(string root)
    {
        foreach (string name in new string[] { "pythonw.exe", "python.exe" })
        {
            string local = Path.Combine(root, ".venv", "Scripts", name);
            if (File.Exists(local)) return local;
        }
        foreach (string name in new string[] { "pythonw.exe", "python.exe" })
        {
            foreach (string entry in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
            {
                string directory = Environment.ExpandEnvironmentVariables(entry.Trim().Trim('"'));
                if (directory.Length == 0) continue;
                string candidate = Path.Combine(directory, name);
                if (File.Exists(candidate)) return candidate;
            }
        }
        throw new InvalidOperationException("Python was not found. Install this project's dependencies as described in README.md.");
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string script = Path.Combine(root, "scripts", "launch-dashboard.py");
            if (!File.Exists(script)) throw new FileNotFoundException("Keep Start Dashboard.exe inside the data-engineering folder.");
            bool noBrowser = Array.IndexOf(args, "--no-browser") >= 0;
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = FindPython(root);
            start.Arguments = "\"" + script + "\"" + (noBrowser ? " --no-browser" : "");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            using (Process process = Process.Start(start)) { }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "SwarmLink dashboard could not start", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
